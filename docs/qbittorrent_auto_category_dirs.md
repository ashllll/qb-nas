# qBittorrent API 自动按分类创建下载目录方案

## 1. 目标

实现以下效果：

```text
分类自动生成目录：
/downloads/电影
/downloads/剧集
/downloads/软件
/downloads/音乐
```

要求：

- 不手动在 NAS 上创建分类文件夹；
- 不在每个下载任务中硬编码 `savepath`；
- 添加下载任务时只传 `category`；
- 分类不存在时自动创建 qBittorrent 分类；
- 分类目录不存在时由脚本自动创建；
- qBittorrent 使用分类的 `savePath` 自动决定最终下载路径。

---

## 2. 官方 API 二次核对结论

已按 qBittorrent 官方 WebUI API 文档核对，本文使用的接口和参数符合官方 API 设计。

| 功能 | 官方接口 | 方法 | 关键参数 | 是否用于本文方案 |
|---|---|---:|---|---:|
| 登录 | `/api/v2/auth/login` | POST | `username`, `password` | 是 |
| 获取分类 | `/api/v2/torrents/categories` | GET | 无 | 是 |
| 创建分类 | `/api/v2/torrents/createCategory` | POST | `category`, `savePath` | 是 |
| 编辑分类路径 | `/api/v2/torrents/editCategory` | POST | `category`, `savePath` | 是 |
| 添加下载任务 | `/api/v2/torrents/add` | POST | `urls`, `category`, `autoTMM` | 是 |
| 设置全局偏好 | `/api/v2/app/setPreferences` | POST | `json` | 可选 |

注意：qBittorrent API 有“创建/编辑分类保存路径”的接口，但它不是通用 NAS 文件管理 API，不能单独当作 `mkdir` 使用。为了保证“完全自动创建真实目录”，脚本应先通过文件系统权限自动创建目录，再调用 qB API 创建/更新分类。

---

## 3. 推荐最终流程

```text
输入：磁力链接 + 分类名
  ↓
清洗分类名，防止路径穿越或非法字符
  ↓
根据分类名生成真实 NAS 目录
例如：Z:\downloads\电影 或 /downloads/电影
  ↓
脚本自动 mkdir，不需要手动创建
  ↓
根据分类名生成 qB 可见路径
例如：/downloads/电影
  ↓
读取 qB 当前分类列表
  ↓
分类不存在：调用 createCategory(category, savePath)
分类存在但 savePath 不一致：调用 editCategory(category, savePath)
  ↓
添加下载任务：只传 category + autoTMM=true
  ↓
不传 savepath
  ↓
qB 根据分类 savePath 自动保存到分类目录
```

---

## 4. 路径设计

需要区分两个路径：

| 路径 | 作用 | 示例 |
|---|---|---|
| `FS_BASE_PATH` | 脚本用于自动创建真实目录的路径 | `Z:\downloads` 或 `/downloads` |
| `QBT_BASE_PATH` | qBittorrent 进程内部看到的下载路径 | `/downloads` |

### 4.1 qB 跑在 NAS Docker 中，脚本也跑在同一个 Docker / NAS 环境

```python
FS_BASE_PATH = "/downloads"
QBT_BASE_PATH = "/downloads"
```

最终生成：

```text
/downloads/电影
/downloads/剧集
/downloads/软件
/downloads/音乐
```

### 4.2 qB 跑在 NAS Docker 中，脚本跑在 Windows 上

假设 NAS 下载目录通过 SMB 挂载为 Windows 的 `Z:` 盘：

```python
FS_BASE_PATH = r"Z:\downloads"
QBT_BASE_PATH = "/downloads"
```

此时：

```text
脚本真实创建：Z:\downloads\电影
qB API 保存路径：/downloads/电影
```

这两个路径字符串不同，但必须指向同一份 NAS 目录。

---

## 5. 可直接使用的 Python 脚本

安装依赖：

```bash
python -m pip install requests
```

脚本：

```python
import json
import re
from pathlib import Path, PurePosixPath
from typing import Dict, Tuple

import requests


class QBitAutoCategoryDownloader:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        fs_base_path: str,
        qbt_base_path: str,
    ):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password

        # 脚本实际 mkdir 使用的路径
        self.fs_base_path = Path(fs_base_path)

        # qBittorrent API 中使用的路径，通常建议使用 POSIX 风格
        self.qbt_base_path = qbt_base_path.rstrip("/")

        self.session = requests.Session()

    def login(self) -> None:
        resp = self.session.post(
            f"{self.host}/api/v2/auth/login",
            data={
                "username": self.username,
                "password": self.password,
            },
            headers={"Referer": self.host},
            timeout=10,
        )
        resp.raise_for_status()

        if "SID" not in self.session.cookies.get_dict():
            raise RuntimeError(f"qBittorrent 登录失败，响应内容：{resp.text}")

    @staticmethod
    def sanitize_category(category: str) -> str:
        """
        清洗分类名，避免出现路径穿越和非法路径字符。
        例如：
        movie/2024 -> movie_2024
        ../电影     -> 电影
        """
        category = category.strip()
        category = category.replace("\\", "_").replace("/", "_")
        category = re.sub(r"^\.+", "", category)
        category = re.sub(r'[<>:"|?*]', "_", category)
        category = category.strip(" .")

        if not category:
            raise ValueError("分类名不能为空")

        return category

    def build_paths(self, category: str) -> Tuple[str, Path, str]:
        safe_category = self.sanitize_category(category)

        # 脚本实际创建的 NAS 目录
        fs_category_path = self.fs_base_path / safe_category

        # qB API 中登记的分类保存目录
        qbt_category_path = str(PurePosixPath(self.qbt_base_path) / safe_category)

        return safe_category, fs_category_path, qbt_category_path

    def ensure_real_folder(self, fs_category_path: Path) -> None:
        """
        自动创建 NAS 真实目录。
        这一步不是 qB API 做的，而是脚本通过 NAS 挂载路径完成。
        """
        fs_category_path.mkdir(parents=True, exist_ok=True)

    def get_categories(self) -> Dict:
        resp = self.session.get(
            f"{self.host}/api/v2/torrents/categories",
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def ensure_qbt_category(self, category: str, qbt_category_path: str) -> None:
        categories = self.get_categories()

        if category not in categories:
            resp = self.session.post(
                f"{self.host}/api/v2/torrents/createCategory",
                data={
                    "category": category,
                    "savePath": qbt_category_path,
                },
                headers={"Referer": self.host},
                timeout=10,
            )
            resp.raise_for_status()
            return

        current_save_path = categories[category].get("savePath", "").rstrip("/")
        target_save_path = qbt_category_path.rstrip("/")

        if current_save_path != target_save_path:
            resp = self.session.post(
                f"{self.host}/api/v2/torrents/editCategory",
                data={
                    "category": category,
                    "savePath": qbt_category_path,
                },
                headers={"Referer": self.host},
                timeout=10,
            )
            resp.raise_for_status()

    def ensure_category_folder_and_qbt_category(self, category: str) -> Tuple[str, Path, str]:
        safe_category, fs_category_path, qbt_category_path = self.build_paths(category)

        # 1. 自动创建真实 NAS 分类目录
        self.ensure_real_folder(fs_category_path)

        # 2. 自动创建或更新 qB 分类
        self.ensure_qbt_category(safe_category, qbt_category_path)

        return safe_category, fs_category_path, qbt_category_path

    def configure_auto_tmm(self) -> None:
        """
        可选：开启 qB 全局自动种子管理相关配置。
        只设置必要字段，不覆盖其他配置。
        """
        prefs = {
            "auto_tmm_enabled": True,
            "torrent_changed_tmm_enabled": True,
            "category_changed_tmm_enabled": True,
            "save_path_changed_tmm_enabled": True,
        }

        resp = self.session.post(
            f"{self.host}/api/v2/app/setPreferences",
            data={"json": json.dumps(prefs, ensure_ascii=False)},
            headers={"Referer": self.host},
            timeout=10,
        )
        resp.raise_for_status()

    def add_magnet(self, magnet: str, category: str, paused: bool = False) -> str:
        safe_category, fs_path, qbt_path = self.ensure_category_folder_and_qbt_category(category)

        # 添加任务时不要传 savepath，让 qB 使用分类 savePath。
        resp = self.session.post(
            f"{self.host}/api/v2/torrents/add",
            data={
                "urls": magnet,
                "category": safe_category,
                "autoTMM": "true",
                "paused": str(paused).lower(),
            },
            headers={"Referer": self.host},
            timeout=10,
        )
        resp.raise_for_status()

        print(f"分类：{safe_category}")
        print(f"真实目录：{fs_path}")
        print(f"qB 分类路径：{qbt_path}")
        print(f"qB 响应：{resp.text}")

        return resp.text


if __name__ == "__main__":
    qbt = QBitAutoCategoryDownloader(
        host="http://127.0.0.1:8080",
        username="admin",
        password="你的密码",

        # 脚本用于自动 mkdir 的真实路径。
        # 如果脚本跑在 Windows，并通过 SMB 挂载 NAS 下载目录：
        # fs_base_path=r"Z:\downloads",
        # 如果脚本跑在 NAS / Docker 内部：
        fs_base_path="/downloads",

        # qBittorrent 进程能看到的路径。
        # Docker 场景通常是 /downloads。
        qbt_base_path="/downloads",
    )

    qbt.login()

    # 可选：只需要执行一次，也可以每次启动时执行，属于幂等配置。
    qbt.configure_auto_tmm()

    qbt.add_magnet(
        magnet="magnet:?xt=urn:btih:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        category="电影",
        paused=False,
    )
```

---

## 6. 添加多个分类的示例

```python
categories = ["电影", "剧集", "软件", "音乐"]

for category in categories:
    qbt.ensure_category_folder_and_qbt_category(category)
```

执行后会自动确保：

```text
/downloads/电影
/downloads/剧集
/downloads/软件
/downloads/音乐
```

同时 qB 中也会存在对应分类：

```text
电影 -> /downloads/电影
剧集 -> /downloads/剧集
软件 -> /downloads/软件
音乐 -> /downloads/音乐
```

---

## 7. curl 调试示例

### 7.1 登录

```bash
HOST="http://127.0.0.1:8080"

curl -i \
  -H "Referer: $HOST" \
  -c qbit.cookie \
  -d "username=admin&password=你的密码" \
  "$HOST/api/v2/auth/login"
```

### 7.2 查看已有分类

```bash
curl -b qbit.cookie \
  "$HOST/api/v2/torrents/categories"
```

### 7.3 创建分类

```bash
curl -b qbit.cookie \
  -H "Referer: $HOST" \
  -d "category=电影&savePath=/downloads/电影" \
  "$HOST/api/v2/torrents/createCategory"
```

### 7.4 修改分类路径

```bash
curl -b qbit.cookie \
  -H "Referer: $HOST" \
  -d "category=电影&savePath=/downloads/电影" \
  "$HOST/api/v2/torrents/editCategory"
```

### 7.5 添加任务，不传 savepath

```bash
curl -b qbit.cookie \
  -H "Referer: $HOST" \
  -F "urls=magnet:?xt=urn:btih:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -F "category=电影" \
  -F "autoTMM=true" \
  -F "paused=false" \
  "$HOST/api/v2/torrents/add"
```

---

## 8. 关键注意事项

### 8.1 不要在添加任务时传 `savepath`

错误做法：

```python
qbt.add_magnet(
    magnet=magnet,
    category="电影",
    savepath="/downloads/电影",
)
```

推荐做法：

```python
qbt.add_magnet(
    magnet=magnet,
    category="电影",
)
```

路径管理统一交给分类的 `savePath`。

### 8.2 qB API 路径必须以 qB 进程视角为准

如果 qB 跑在 Docker 容器里，API 里的 `savePath` 应该写容器内路径，例如：

```text
/downloads/电影
```

而不是 NAS 宿主机路径：

```text
/volume1/downloads/电影
```

除非 qB 进程本身确实能看到 `/volume1/downloads/电影`。

### 8.3 真正的自动建目录由脚本完成

qB API 官方文档只定义了 `createCategory` / `editCategory` 的 `savePath` 参数，并没有把它定义为通用文件系统 `mkdir` 接口。

因此为了确保目录一定存在，推荐脚本执行：

```python
Path(fs_category_path).mkdir(parents=True, exist_ok=True)
```

然后再调用 qB API。

---

## 9. 最终结论

你的需求可以实现，而且推荐这样做：

```text
基础路径只配置一次：
/downloads

分类名动态输入：
电影 / 剧集 / 软件 / 音乐

脚本自动生成真实目录：
/downloads/电影
/downloads/剧集
/downloads/软件
/downloads/音乐

脚本自动创建 qB 分类：
电影 -> /downloads/电影
剧集 -> /downloads/剧集
软件 -> /downloads/软件
音乐 -> /downloads/音乐

添加任务时只传：
category + autoTMM

不再传：
savepath
```

这就是“不手动创建分类文件夹、不硬编码每个下载路径、完全按分类自动生成目录”的实现方式。
