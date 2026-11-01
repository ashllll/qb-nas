"""IngestService — Mac 上报的入口，也是「网址 → 磁力」任务的执行者。

职责边界（默认 A）：

1. 磁力：解析 → 归一化 infohash → 按 infohash 去重 → 走现有分类规则链 → 写 SQLite 资源库。
   **不下载**；qB 只在用户点选后由 ``/api/download`` 提交。
2. 网址：URL 安全校验 → 落一个持久化的抓取任务 → 交给 ``MagnetCrawler.crawl`` 事件流，
   边找到边入库、边分类（首条不等整轮）；每条失败原因记进任务，UI 能说清为什么没有结果。
3. 幂等：``event_id`` 重复不再处理；同一个网址已有进行中的任务时复用，不重复抓。
4. 续跑：任务的排队/进行状态落在 SQLite；服务重启后 ``resume_pending()`` 把没干完的活重新排上。

取页解析的内部实现属于取页解析块（``scrapling_spider.py`` / ``crawler.py``）；这里只消费
``crawl()`` 产出的事件，不碰它的内部。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from typing import Optional

from magnet_harvester.bus import Event, EventType, MessageBus
from magnet_harvester.classifier.local_classifier import LocalClassifier
from magnet_harvester.config import settings
from magnet_harvester.context.app_context import BackgroundTaskSpawner
from magnet_harvester.jobs import CrawlJob, JobStatus, JobStore
from magnet_harvester.magnet_parser import parse_magnet
from magnet_harvester.models import IngestKind, IngestRequest, MagnetItem, TaskStatus
from magnet_harvester.store import ItemStore
from magnet_harvester.transitions import ClassificationTransitions, DiscoveryTransitions
from magnet_harvester.utils.infohash import normalize_infohash
from magnet_harvester.utils.timeutil import iso_utc, utc_now
from magnet_harvester.utils.url_validator import URLValidationError, validate_crawl_url
from magnet_harvester.utils.bg_tasks import BGTaskManager

log = logging.getLogger(__name__)

_INGEST_ERROR_REASONS = {
    "no_magnet",
    "blocked",
    "timeout",
    "http_error",
    "parse_error",
    "unsafe_url",
    "browser_unavailable",
    # 取页层没给结构化原因时的兜底，UI 直接显示原始消息
    "crawl_error",
}

# 一条任务最多留多少条错误明细（站点被限流时错误会成百上千，只留最近的）
_MAX_JOB_ERRORS = 20


class IngestService:
    """上报入口 + 抓取任务执行器。"""

    def __init__(
        self,
        *,
        store: ItemStore,
        jobs: JobStore,
        classifier: LocalClassifier,
        crawler,
        bus: MessageBus,
        discovery: DiscoveryTransitions,
        classification: ClassificationTransitions,
        task_manager: BackgroundTaskSpawner | None = None,
        job_depth: int = 2,
        job_concurrency: int = 2,
        max_attempts: int = 2,
    ):
        self._store = store
        self._jobs = jobs
        self._classifier = classifier
        self._crawler = crawler
        self._bus = bus
        self._discovery = discovery
        self._classification = classification
        self._task_manager = task_manager
        self._job_depth = max(1, int(job_depth))
        self._max_attempts = max(1, int(max_attempts))
        self._semaphore = asyncio.Semaphore(max(1, int(job_concurrency)))
        self._running: dict[str, asyncio.Task] = {}
        # 同一 event_id 的处理串行化：Mac 重发/并发重试时，第二次必须看到第一次记下的结果，
        # 而不是两条并行处理各自「看到还没处理过」。有上限，长期运行不会积累。
        self._event_locks: dict[str, asyncio.Lock] = {}
        self._event_locks_guard = asyncio.Lock()
        self._MAX_EVENT_LOCKS = 1024

    # ── 上报入口 ──────────────────────────

    async def handle_event(self, req: IngestRequest, *, source: str = "mac") -> dict:
        """处理一条上报事件；返回 ``{ok,status,item_id,job_id,reason}``。

        幂等键是 ``event_id``：重发只处理一次并原样返回第一次的结果；同一 event_id
        带着不同内容再来，按客户端事件号重复拒掉，绝不覆盖第一次的记录。
        """
        event_id = req.event_id or uuid.uuid4().hex
        value_hash = hashlib.sha256(req.value.encode("utf-8", "replace")).hexdigest()[:16]

        lock = await self._event_lock(event_id)
        async with lock:
            return await self._handle_event_once(
                req, event_id=event_id, value_hash=value_hash, source=source
            )

    async def _event_lock(self, event_id: str) -> asyncio.Lock:
        """按 event_id 取一把串行锁（有上限，满了先清掉没人持有的）。"""
        async with self._event_locks_guard:
            lock = self._event_locks.get(event_id)
            if lock is None:
                if len(self._event_locks) >= self._MAX_EVENT_LOCKS:
                    for key, candidate in list(self._event_locks.items()):
                        if not candidate.locked():
                            del self._event_locks[key]
                            break
                lock = asyncio.Lock()
                self._event_locks[event_id] = lock
            return lock

    async def _handle_event_once(
        self,
        req: IngestRequest,
        *,
        event_id: str,
        value_hash: str,
        source: str,
    ) -> dict:
        previous = self._jobs.get_event(event_id)
        if previous is not None:
            if previous.value_hash and previous.value_hash != value_hash:
                log.warning(
                    "event_id 重复但内容不同，拒绝处理：event_id=%s kind=%s",
                    event_id,
                    req.kind.value,
                )
                return {
                    "ok": False,
                    "status": "rejected",
                    "reason": "同一 event_id 已用于另一条上报（客户端事件号重复），本次内容未处理",
                    "event_id": event_id,
                }
            response = previous.as_response()
            if previous.status == "accepted":
                response["status"] = "duplicate"
                response["reason"] = "同一 event_id 已处理过（幂等忽略）"
            return response

        if req.kind == IngestKind.magnet:
            result = await self.ingest_magnet_text(
                req.value,
                source=source,
                batch_id=event_id,
            )
        else:
            result = await self.ingest_url(req.value, source=source, batch_id=event_id)

        self._jobs.record_event(
            event_id=event_id,
            kind=req.kind.value,
            value_hash=value_hash,
            status=result.get("status", "rejected"),
            item_id=result.get("item_id") or "",
            job_id=result.get("job_id") or "",
            reason=result.get("reason") or "",
        )
        response = dict(result)
        response["event_id"] = event_id
        await self._bus.emit(
            Event(
                EventType.INGEST_RECEIVED,
                {
                    "event_id": event_id,
                    "kind": req.kind.value,
                    "status": response.get("status", ""),
                    "item_id": response.get("item_id"),
                    "job_id": response.get("job_id"),
                    "device": req.device or "",
                },
            )
        )
        return response

    async def ingest_magnet_text(
        self,
        value: str,
        *,
        source: str = "mac",
        batch_id: Optional[str] = None,
        source_url: str = "",
    ) -> dict:
        """把一条磁力文本（或其中一条 magnet 链接）入库并分类；不下载。"""
        parsed = parse_magnet(value)
        if parsed is None:
            return {"ok": False, "status": "rejected", "reason": "不是有效的磁力链接（缺少 btih）"}

        normalized = normalize_infohash(parsed.get("hash"))
        if not normalized:
            return {
                "ok": False,
                "status": "rejected",
                "reason": "无法识别的 infohash（既不是 40 位 hex 也不是 32 位 base32）",
            }

        existing = await self._store.get(normalized)
        if existing is not None:
            return {
                "ok": True,
                "status": "duplicate",
                "item_id": normalized,
                "reason": "库里已有同一 infohash",
            }

        item = MagnetItem(
            hash=normalized,
            name=parsed.get("name") or f"Unknown_{normalized[:8]}",
            magnet=parsed.get("magnet") or value.strip(),
            size=parsed.get("size"),
            source_url=source_url,
            batch_id=batch_id,
            source_kind=f"{source}_magnet",
            status=TaskStatus.pending,
        )
        added = await self._discovery.found(item)
        if not added:
            return {
                "ok": True,
                "status": "duplicate",
                "item_id": normalized,
                "reason": "库里已有同一 infohash",
            }
        await self._classify_one(item.hash, item.name)
        return {"ok": True, "status": "accepted", "item_id": normalized, "reason": ""}

    async def ingest_magnet(self, item: MagnetItem) -> bool:
        """已在别处解析好的条目入库并分类（供 pipeline.ingest 复用）。"""
        added = await self._discovery.found(item)
        if added:
            await self._classify_one(item.hash, item.name)
        return added

    async def ingest_url(
        self,
        url: str,
        *,
        source: str = "mac",
        batch_id: Optional[str] = None,
        depth: Optional[int] = None,
        auto_start: bool = True,
    ) -> dict:
        """校验网址 → 建任务 → 异步抓取。返回 ``job_id``。"""
        url = (url or "").strip()
        if not url:
            return {"ok": False, "status": "rejected", "reason": "网址为空"}
        try:
            validate_crawl_url(url, allow_fake_ip=settings.CRAWLER_ALLOW_FAKE_IP)
        except URLValidationError as exc:
            return {
                "ok": False,
                "status": "rejected",
                "reason": f"不安全的网址：{exc}",
            }

        active = self._jobs.find_active_by_url(url)
        if active is not None:
            return {
                "ok": True,
                "status": "accepted",
                "job_id": active.job_id,
                "reason": "该网址正在抓取，复用同一任务",
            }

        job = CrawlJob(
            job_id=uuid.uuid4().hex,
            batch_id=batch_id or uuid.uuid4().hex,
            url=url,
            depth=max(1, min(int(depth or self._job_depth), self._max_depth())),
            status=JobStatus.queued,
            source=source,
            message="等待抓取",
        )
        self._jobs.create(job)
        await self._emit_job(job)
        if auto_start:
            self.start_job(job.job_id)
        return {"ok": True, "status": "accepted", "job_id": job.job_id, "reason": ""}

    # ── 任务执行 ──────────────────────────

    def start_job(self, job_id: str) -> bool:
        """把任务排到后台执行（同一任务不会重复启动）。"""
        if job_id in self._running and not self._running[job_id].done():
            return False
        if self._task_manager is None:
            log.warning("task_manager 未配置，无法启动抓取任务 %s", job_id)
            return False
        try:
            task = BGTaskManager.spawn(
                self._run_job(job_id), task_manager=self._task_manager, name=f"crawl-job:{job_id}"
            )
        except RuntimeError as exc:
            log.warning("无法启动抓取任务 %s: %s", job_id, exc)
            return False
        self._running[job_id] = task
        return True

    async def resume_pending(self) -> int:
        """服务启动时把中断的任务重新排上，返回续跑数量。"""
        try:
            job_ids = self._jobs.claim_resumable(max_attempts=self._max_attempts)
        except Exception:
            log.exception("读取待续跑任务失败")
            return 0
        started = 0
        for job_id in job_ids:
            if self.start_job(job_id):
                started += 1
        if started:
            log.info("续跑 %d 个未完成的抓取任务", started)
        return started

    async def shutdown(self) -> None:
        """关服务时把进行中的任务退回 queued，等下次启动续跑。"""
        pending = [task for task in self._running.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._running.clear()

    async def retry_job(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "status": "rejected", "reason": "任务不存在"}
        if job.status in JobStatus.ACTIVE:
            return {"ok": True, "status": "accepted", "job_id": job_id, "reason": "任务仍在进行中"}
        self._jobs.update(
            job_id,
            status=JobStatus.queued,
            reason="",
            message="重新抓取",
            attempts=0,
            finished_at="",
        )
        updated = self._jobs.get(job_id)
        if updated is not None:
            await self._emit_job(updated)
        self.start_job(job_id)
        return {"ok": True, "status": "accepted", "job_id": job_id, "reason": "已重新排队"}

    async def _run_job(self, job_id: str) -> None:
        """跑一个抓取任务。

        ``_running`` 的清理放在最外层 finally：中途任何一步（读库/写库/发事件）抛错，
        都不能在 ``_running`` 里留下一个「看起来还在跑」的僵尸任务——否则这个 job_id
        再也不会被重新启动，只能等重启。
        """
        try:
            await self._run_job_inner(job_id)
        finally:
            self._running.pop(job_id, None)

    async def _run_job_inner(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        if job.status == JobStatus.running:
            log.debug("任务 %s 已在运行，跳过重复启动", job_id)
            return

        async with self._semaphore:
            started_wall = time.monotonic()
            job = (
                self._jobs.update(
                    job_id,
                    status=JobStatus.running,
                    started_at=iso_utc(utc_now()),
                    reason="",
                    message="正在找磁力…",
                    finished_at="",
                )
                or job
            )
            await self._emit_job(job)

            counters = {"found": 0, "added": 0, "duplicates": 0, "pages": 0}
            errors: list[dict] = []
            first_item_ms: Optional[int] = None
            browser_used = False
            done_metrics: dict = {}
            fatal = ""

            try:
                depth = max(1, min(int(job.depth or self._job_depth), self._max_depth()))
                async for msg in self._crawler.crawl(job.url, depth=depth):
                    kind = msg.get("type")
                    if kind == "found":
                        result = await self._handle_found(job, msg.get("item") or {})
                        counters["found"] += 1
                        if result == "added":
                            counters["added"] += 1
                            if first_item_ms is None:
                                first_item_ms = int((time.monotonic() - started_wall) * 1000)
                        else:
                            counters["duplicates"] += 1
                        job = (
                            self._jobs.update(
                                job_id,
                                **counters,
                                first_item_ms=first_item_ms,
                                message=f"已收到 {counters['added']} 条",
                            )
                            or job
                        )
                        await self._emit_job(job)
                    elif kind == "progress":
                        metrics = msg.get("metrics") or {}
                        if isinstance(metrics, dict):
                            if metrics.get("pages_crawled"):
                                counters["pages"] = int(metrics["pages_crawled"])
                            if metrics.get("browser_used"):
                                browser_used = True
                        counters["pages"] = max(counters["pages"], int(msg.get("pages") or 0))
                        job = self._jobs.update(job_id, pages=counters["pages"]) or job
                    elif kind == "error":
                        entry = {
                            "msg": str(msg.get("msg") or msg.get("message") or "抓取出错"),
                            "url": str(msg.get("url") or ""),
                            "reason": self._normalize_reason(
                                msg.get("reason"),
                                str(msg.get("msg") or msg.get("message") or ""),
                            ),
                        }
                        errors.append(entry)
                        if len(errors) > _MAX_JOB_ERRORS:
                            del errors[: len(errors) - _MAX_JOB_ERRORS]
                        job = (
                            self._jobs.update(
                                job_id,
                                errors=errors,
                                # reason 只放原因代码（机器判读）；具体消息在 errors[]，中文在 reason_label
                                reason=entry["reason"],
                            )
                            or job
                        )
                        await self._emit_job(job)
                    elif kind == "done":
                        done_metrics = msg.get("metrics") or {}
                        counters["pages"] = max(
                            counters["pages"], int(done_metrics.get("pages_crawled") or 0)
                        )
                        if done_metrics.get("browser_used"):
                            browser_used = True
            except asyncio.CancelledError:
                self._jobs.update(
                    job_id,
                    status=JobStatus.queued,
                    message="服务关闭，重启后继续",
                    **counters,
                )
                raise
            except Exception as exc:  # 爬虫内部抛错也要留下原因
                log.exception("抓取任务异常 job=%s url=%s", job_id, job.url)
                fatal = f"抓取过程异常：{exc}"
            finally:
                elapsed_ms = int((time.monotonic() - started_wall) * 1000)

            if fatal:
                final = self._jobs.update(
                    job_id,
                    status=JobStatus.error,
                    reason=fatal,
                    message="抓取失败",
                    elapsed_ms=elapsed_ms,
                    **counters,
                )
            else:
                reason = ""
                if counters["found"] == 0:
                    reason = errors[-1]["reason"] if errors else "no_magnet"
                final = self._jobs.update(
                    job_id,
                    status=JobStatus.done,
                    reason=reason,
                    message="",
                    elapsed_ms=elapsed_ms,
                    browser_used=browser_used,
                    errors=errors,
                    finished_at=iso_utc(utc_now()),
                    **counters,
                )
            if final is not None:
                await self._emit_job(final)

    async def _handle_found(self, job: CrawlJob, raw: dict) -> str:
        """处理一条 found 事件：归一化、去重、入库、立即分类。返回 added/duplicate。"""
        normalized = normalize_infohash(raw.get("hash"))
        if not normalized:
            log.warning("抓到无法识别的 infohash，跳过：%s", str(raw)[:200])
            return "duplicate"

        detail_url = str(raw.get("detail_url") or "").strip()
        page_url = str(raw.get("source_url") or "").strip()
        if not detail_url and page_url and page_url != job.url:
            detail_url = page_url

        existing = await self._store.get(normalized)
        if existing is not None:
            return "duplicate"

        item = MagnetItem(
            hash=normalized,
            name=str(raw.get("name") or f"Unknown_{normalized[:8]}"),
            magnet=str(raw.get("magnet") or f"magnet:?xt=urn:btih:{normalized}"),
            size=raw.get("size"),
            source_url=job.url,
            detail_url=detail_url or None,
            batch_id=job.batch_id,
            source_kind=f"{job.source}_url" if job.source else "crawl",
            status=TaskStatus.pending,
        )
        added = await self._discovery.found(item)
        if not added:
            return "duplicate"
        await self._classify_one(item.hash, item.name)
        return "added"

    async def _classify_one(self, hash_key: str, name: str) -> None:
        """单条即时分类：让「边抓边出」的条目立刻有分类，而不是等整轮抓完。"""
        try:
            result = await asyncio.to_thread(self._classifier.classify_one, name)
        except Exception as exc:
            log.warning("分类失败 hash=%s: %s", hash_key, exc)
            await self._classification.failed(hash_key, f"分类失败：{exc}")
            return
        await self._classification.started(hash_key)
        await self._classification.classified(hash_key, result or {})

    # ── 任务查询 ──────────────────────────

    def list_jobs(self, *, limit: int = 20, status: Optional[str] = None) -> list[dict]:
        return [job.as_dict() for job in self._jobs.list(limit=limit, status=status)]

    def job_counts(self) -> dict:
        return self._jobs.counts()

    def get_job(self, job_id: str) -> Optional[dict]:
        job = self._jobs.get(job_id)
        return job.as_dict() if job is not None else None

    # ── 快照 ──────────────────────────────

    async def status_snapshot(self, *, jobs_limit: int = 10) -> dict:
        counts = self._jobs.counts()
        recent_jobs = [job.as_dict() for job in self._jobs.list(limit=jobs_limit)]
        try:
            recent_items = [item.model_dump() for item in await self._store.recent(5)]
        except Exception:
            recent_items = []
        for payload in recent_items:
            payload["status"] = (
                payload["status"].value
                if isinstance(payload["status"], TaskStatus)
                else payload["status"]
            )
        return {
            "ok": True,
            "last_event_at": self._jobs.last_event_at(),
            "jobs": recent_jobs,
            "job_counts": counts,
            "recent_items": recent_items,
            "recent_events": [
                {
                    "event_id": record.event_id,
                    "kind": record.kind,
                    "status": record.status,
                    "item_id": record.item_id,
                    "job_id": record.job_id,
                    "reason": record.reason,
                    "created_at": record.created_at,
                }
                for record in self._jobs.recent_events(8)
            ],
        }

    # ── 内部小工具 ────────────────────────

    def _max_depth(self) -> int:
        try:
            return max(1, int(self._crawler.max_depth))
        except Exception:
            return self._job_depth

    @classmethod
    def _normalize_reason(cls, reason, message: str = "") -> str:
        """统一失败原因的取值：取页层给了结构化原因就用它，否则按消息猜一个。"""
        text = str(reason or "").strip()
        if text in _INGEST_ERROR_REASONS:
            return text
        if text:
            return text
        lowered = (message or "").lower()
        if "blocked" in lowered or "403" in lowered or "just a moment" in lowered:
            return "blocked"
        if "timeout" in lowered or "超时" in lowered:
            return "timeout"
        return "crawl_error"

    async def _emit_job(self, job: CrawlJob) -> None:
        try:
            await self._bus.emit(Event(EventType.JOB_UPDATED, job.as_dict()))
        except Exception:
            log.debug("任务事件发送失败 job=%s", job.job_id, exc_info=True)
