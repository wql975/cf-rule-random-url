# 重构与优化记录

对 wql975/cf-rule-random-url 的审查、重构记录。

## 一、架构重构：Transform Rules → Pages Functions

### 为什么必须改

原方案用 Transform Rule 把 `/pc` 重写为 `/pc/<3位hex>.jpg`：

```
concat(http.request.uri.path, "/", substring(uuidv4(cf.random_seed), 0, 3), ".jpg")
```

`substring(..., 0, 3)` 的值域是 `000`–`fff`，即 **4096 个固定槽位**。随机值可能落在
其中任意一点，所以目录里必须备齐 4096 个文件，否则就会 404。由此产生三个无法回避的问题：

1. **文件数被锁死**：素材只有 1076 张，也必须凑满 4096 个文件
2. **大量重复**：每张图平均重复 3.8 次，体积从 306MB 膨胀到 1.2GB
3. **概率不均**：`itertools.cycle` 按固定顺序填充，排前面的图会固定多出现一次，
   本例中前 868 张出现概率比其余高 **33%**

hex 位数只能取整数（16^N = 256 / 4096 / 65536），无法匹配任意素材数量，
所以这个问题在 Transform Rules 框架内**无解**。

### 改成什么

改用 **Pages Functions**：

- 素材按 `0.jpg` … `N-1.jpg` 命名，**文件数 = 素材数，零重复**
- `functions/[category].js` 收到 `/pc` 时随机取序号，302 跳转到 `/pc/<序号>.jpg`
- 图片仍由 Pages 静态 CDN 提供，流量免费无限

效果：

| | 旧版 | 新版 |
| --- | --- | --- |
| 文件数 | 4096 | **1076** |
| 体积 | 1.2 GB | **309 MB**（-75%） |
| 重复填充 | 每张 3.8 次 | **无** |
| 概率分布 | 不均（相差 33%） | **完全均等** |
| 请求开销 | 无 | 1 次 Functions 调用/请求 |

### 免费版额度占用（重点）

| 资源 | 免费额度 | 本方案占用 |
| --- | --- | --- |
| Pages 静态存储 | 20000 文件、单文件 25MB | 1076 文件、最大 0.76MB |
| Pages 流量 | 不限 | 不限 |
| **Workers KV** | **1 GB** | **0（完全不使用）** |
| R2 | 10 GB | 0（不使用） |
| Pages Functions | 10 万请求/天 | 1 次/请求 |

**没有使用 KV。** 分类与图片数量的对应关系内联在 `functions/_counts.js` 里，
只是一个几十字节的 JS 对象，随代码一起部署，不占任何存储配额。
图片列表也不需要存储——文件名就是序号 `0…N-1`，函数只需知道 N 就能随机。

## 二、修复的缺陷

### 原项目自带

| # | 问题 | 后果 |
| --- | --- | --- |
| 1 | 文件末尾 `if __name__ == "__main__": main()` 写了两遍 | 所有分类生成两遍，耗时翻倍 |
| 2 | `shutil.copy` 把 webp/png 直接存成 `.jpg` | MIME 错配：Content-Type 是 image/jpeg，内容是 WebP。本项目示例素材 979 张全是 webp，正好命中 |
| 3 | `wrangler.jsonc` 的 `assets.directory: "./"` | 会把整个项目连 943MB 原图、`.git` 一起部署 |
| 4 | 临时目录建在 `dist/_tmp/` | 中间产物会被一起部署 |
| 5 | 废弃分类只删文件不删目录 | 删掉的分类仍会被部署 |
| 6 | README 未提缓存 | 浏览器缓存住 `/pc` 后永远同一张图 |

### 本次重构过程中新引入并已修掉

| # | 问题 | 修复 |
| --- | --- | --- |
| 7 | 用 `--only pc` 时会把 `dist/h`、`dist/v` 当过期分类删除 | 判断依据改为 oriImg 的**全部**分类，不受 `--only` 影响 |
| 8 | `Image.open()` 未用 `with`，句柄泄漏 | 改用 `with`，否则 Windows 上会锁住源文件 |
| 9 | 未处理 EXIF 方向 | 加 `ImageOps.exif_transpose`，部分照片不再躺倒 |
| 10 | 透明 PNG 转 JPG 后透明区变黑 | 合成到白底 |
| 11 | 整目录 `rmtree` 触发系统批量删除保护 | 改为覆盖写入 |
| 12 | Windows 上 `shutil.copy2` 遇 `WinError 1224`（文件被杀软扫描占用）直接崩溃 | `safe_copy` 降级为纯 Python 读写 + 重试，失败的整轮结束后再补 |

## 三、已删除的无关/冗余文件

- `categories.json`：来自"一言"文字 API 的遗留配置（动画/漫画/游戏…），
  本项目无任何代码引用，`path` 指向的 `./categories/a/` 根本不存在
- `gen_img.py.orig`：原版备份
- `.nojekyll`：GitHub Pages 专用，对 Cloudflare Pages 无效
- `_example_backup/`：示例素材备份（已移入 `_trash/`，可自行删除）
- 旧的 4096 个 hex 命名文件（已移入 `_trash/dist_pc_hex_old`）

## 四、验证结果

- 生成：1076 个文件，309MB，单文件最大 0.76MB（远低于 25MB 上限）
- Functions 逻辑：Node 模拟 5000 次请求，0 个 404、0 个越界，命中 1069/1076 张，分布均匀
- 未知分类 `/nosuch` 正确返回 404
- 响应头 `Cache-Control: no-store`，避免浏览器缓存导致随机失效

## 五、部署

```bash
npx wrangler pages deploy dist
```

换素材后务必重跑 `python gen_img.py`，`functions/_counts.js` 才会同步更新。
