# Magnet Harvester 架构深化 — TDD 循环计划

## 目标

使用 TDD（红-绿-重构）循环，逐个完成 5 个架构候选项的深化重构。

## 处理顺序（按影响面 + 依赖关系排序）

### 循环 1：Pydantic 模型更新与存储一致性

**文件**: `store.py`, `models.py`  
**问题**: `setattr` 绕过 Pydantic 验证，破坏类型安全  
**TDD 行为**: ItemStore 更新应拒绝非法字段值，保持 MagnetItem 不变性  
**接口变更**: `ItemStore.update()` 签名不变，内部使用 `model_copy()`  
**测试重点**: 边界测试 — 传入非法 status/progress 应被验证/拒绝

### 循环 2：qBittorrent 客户端模块化拆分

**文件**: `qbit_client.py` → `qbit_client/` 包  
**问题**: 535 行上帝类，7 个职责混杂  
**TDD 行为**: 各子模块通过 Protocol 隔离，可独立测试  
**接口变更**: 引入 `QBitAuth`, `QBitTorrents`, `QBitCategories` 子模块  
**测试重点**: 状态映射、路径解析、重试策略可纯单元测试

### 循环 3：爬虫并发控制与生命周期管理

**文件**: `crawler.py`  
**问题**: TaskGroup 竞态、\_global_seen 会话污染  
**TDD 行为**: 每次 crawl() 调用是独立会话，worker 可安全取消  
**接口变更**: `_global_seen` 移到会话级，TaskGroup → gather  
**测试重点**: 并发安全、内存不泄漏、取消不抛 ExceptionGroup

### 循环 4：事件总线与状态转换的背压隔离

**文件**: `bus.py`, `pipeline.py`  
**问题**: 状态转换同步等待事件广播，慢客户端阻塞管道  
**TDD 行为**: 状态更新先完成，事件广播异步不阻塞  
**接口变更**: `MagnetItemTransitions` 内部使用队列 + 超时  
**测试重点**: 状态已更新但事件延迟投递的场景

### 循环 5：配置验证与动态更新的原子性

**文件**: `config.py`, `api/routes.py`  
**问题**: update_qbit() 无验证，replace_qbit() 锁+清理不完整  
**TDD 行为**: 非法配置被拒绝，旧客户端资源被清理  
**接口变更**: `Settings.update_qbit()` 返回验证结果，引入不可变快照  
**测试重点**: 并发更新安全、旧连接池清理

## TDD 循环规则

每轮循环严格遵循：

```
红 → 写一个行为测试（失败）
绿 → 写最少代码让它通过
重构 → 清理代码，加深模块
```

**绝不水平切片** — 不先写所有测试再写所有实现。  
**垂直切片** — 一个行为一个循环，基于上一轮学到的东西调整。

## 当前状态

- [ ] 循环 1: Pydantic 模型更新与存储一致性
- [ ] 循环 2: qBittorrent 客户端模块化拆分
- [ ] 循环 3: 爬虫并发控制与生命周期管理
- [ ] 循环 4: 事件总线与状态转换的背压隔离
- [ ] 循环 5: 配置验证与动态更新的原子性
