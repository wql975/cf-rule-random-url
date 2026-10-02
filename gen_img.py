"""
Cloudflare Pages 随机图片 API —— 静态资源生成脚本（Pages Functions 版）

原理
----
1. 每个分类的素材转成标准 JPEG，按 0.jpg、1.jpg ... N-1.jpg 命名放进 dist/<分类>/。
   文件数 == 素材数，不做任何重复填充。
2. dist 作为静态资产部署到 Cloudflare Pages（图片流量由 Pages CDN 提供，免费无限）。
3. 同时生成 functions/[category].js：收到 /<分类> 请求时随机取一个序号，
   302 跳转到 /<分类>/<序号>.jpg。函数只做一次取随机数，开销极小。

对比旧版（Transform Rules 固定 hex 范围）：
  旧版必须凑满 16^N 个文件（4096），素材少就要大量重复；
  本版文件数 = 素材数，体积可下降数倍，且每张图出现概率完全均等。
  代价：每次请求消耗 1 次 Pages Functions 调用（免费额度 10 万/天）。

用法
----
    python gen_img.py                  # 全部分类
    python gen_img.py --only pc        # 只处理 pc
    python gen_img.py --max-width 1600 --quality 80
"""

import os
import sys
import time
import random
import shutil
import argparse
from pathlib import Path
from PIL import Image, ImageOps

# ---------------- 配置 ----------------
SOURCE_DIR = Path("oriImg")
OUTPUT_DIR = Path("dist")
FUNCTIONS_DIR = Path("functions")
TMP_DIR = Path(".tmp")

OUTPUT_EXT = ".jpg"
RANDOM_SEED = 20250102

MAX_WIDTH = 1920
JPEG_QUALITY = 85
DIRECT_COPY_MAX_MB = 0.5     # 已是 JPEG / 宽度达标 / 体积小 -> 直接复制，避免二次编码

CF_MAX_FILE_MB = 25          # Pages 免费版单文件上限
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

REUSE_PREPARED = False


# ---------------- 工具 ----------------
def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def safe_copy(src: Path, dst: Path) -> bool:
    """复制文件，失败返回 False（Windows 上目标被杀软扫描时会短暂占用）"""
    for attempt in range(3):
        try:
            shutil.copy2(src, dst)
            return True
        except OSError:
            try:
                with open(src, "rb") as f:
                    data = f.read()
                with open(dst, "wb") as f:
                    f.write(data)
                return True
            except OSError:
                if attempt < 2:
                    time.sleep(0.3 * (attempt + 1))
    return False


def to_standard_jpeg(src: Path, dst: Path):
    """任意格式 -> 标准 JPEG：修正 EXIF 方向、透明区补白、限宽、压缩"""
    with Image.open(src) as im:
        im.load()
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if im.width > MAX_WIDTH:
            im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
        im.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)


def prepare_sources(images: list, tmp_dir: Path) -> list:
    """预处理：统一转成标准 JPEG 放进临时目录"""
    ensure_dir(tmp_dir)
    prepared, skipped = [], []
    for i, src in enumerate(images, 1):
        out = tmp_dir / f"{i:05d}{OUTPUT_EXT}"
        try:
            with Image.open(src) as probe:          # 必须 with，否则 Windows 句柄泄漏
                width_ok = probe.width <= MAX_WIDTH
            if (src.suffix.lower() in (".jpg", ".jpeg")
                    and src.stat().st_size / 1048576 <= DIRECT_COPY_MAX_MB
                    and width_ok):
                shutil.copy2(src, out)
            else:
                to_standard_jpeg(src, out)
            prepared.append(out)
        except Exception as e:
            skipped.append(src.name)
    if skipped:
        print(f"    跳过 {len(skipped)} 张无法处理: {', '.join(skipped[:5])}")
    return prepared


# ---------------- 生成 Functions ----------------
def write_index(counts: dict):
    """生成一个简单的索引页，避免访问根域名时看到裸 404"""
    items = "".join(
        f'<li><a href="/{c}">/{c}</a> <span>{n} 张</span></li>'
        for c, n in sorted(counts.items())
    )
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Random Image API</title>
<style>
body{{font:15px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:640px;margin:10vh auto;padding:0 20px;color:#222}}
h1{{font-size:20px}} code{{background:#f4f4f5;padding:2px 6px;border-radius:4px}}
li{{list-style:none;padding:6px 0;border-bottom:1px solid #eee}}
span{{color:#888;font-size:13px;margin-left:8px}}
</style>
<h1>Random Image API</h1>
<p>访问下面的分类路径即可获得一张随机图片（302 跳转）：</p>
<ul>{items}</ul>
<p>示例：<code>/pc</code> 会跳转到 <code>/pc/&lt;随机序号&gt;.jpg</code></p>
"""
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
    print("  已生成 dist/index.html")


def write_functions(counts: dict):
    """
    生成 Cloudflare Pages Functions：
      _counts.js     分类 -> 图片数量（下划线开头，不作为路由）
      [category].js  动态路由，随机 302 跳转到对应图片
    """
    ensure_dir(FUNCTIONS_DIR)

    counts_js = "// 自动生成，请勿手改；重新运行 gen_img.py 会覆盖\n"
    counts_js += "export const COUNTS = {\n"
    for cat, n in sorted(counts.items()):
        counts_js += f"  {cat!r}: {n},\n"
    counts_js += "};\n"
    (FUNCTIONS_DIR / "_counts.js").write_text(counts_js, encoding="utf-8")

    router = '''// 自动生成，请勿手改；重新运行 gen_img.py 会覆盖
// 匹配 /<分类> ：随机跳转到该分类下的一张图片
import { COUNTS } from './_counts.js';

export async function onRequest(context) {
  const category = context.params.category;
  const count = COUNTS[category];

  if (!count) {
    return new Response('Not Found', { status: 404 });
  }

  const index = Math.floor(Math.random() * count);
  const url = new URL(`/${category}/${index}.jpg`, context.request.url);

  return new Response(null, {
    status: 302,
    headers: {
      Location: url.pathname,
      // 禁止缓存，否则浏览器会一直跳到同一张图
      'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
    },
  });
}
'''
    (FUNCTIONS_DIR / "[category].js").write_text(router, encoding="utf-8")
    print(f"  已生成 {FUNCTIONS_DIR}/_counts.js 与 {FUNCTIONS_DIR}/[category].js")


# ---------------- 主流程 ----------------
def process_category(category: str, images: list):
    t0 = time.time()
    out_dir = OUTPUT_DIR / category
    ensure_dir(out_dir)

    rng = random.Random(RANDOM_SEED)
    print(f"  [{category}] 素材 {len(images)} 张")

    tmp_dir = TMP_DIR / category
    prepared = None
    if REUSE_PREPARED and tmp_dir.exists():
        existing = sorted(tmp_dir.glob(f"*{OUTPUT_EXT}"))
        if len(existing) == len(images):
            print(f"    复用已有预处理结果 {len(existing)} 张")
            prepared = existing
    if prepared is None:
        print("    预处理中...")
        prepared = prepare_sources(images, tmp_dir)

    if not prepared:
        print(f"  [{category}] 无可用图片，跳过")
        return 0

    rng.shuffle(prepared)      # 打乱，避免相邻序号总是同一批图

    print(f"    生成 {len(prepared)} 个文件（文件数 = 素材数，无重复）...")
    failed, max_sz = [], 0
    for i, src in enumerate(prepared):
        dst = out_dir / f"{i}{OUTPUT_EXT}"
        if safe_copy(src, dst):
            max_sz = max(max_sz, dst.stat().st_size)
        else:
            failed.append((src, dst))
        if (i + 1) % 500 == 0:
            print(f"      {i+1}/{len(prepared)}")

    for rnd in range(4):
        if not failed:
            break
        time.sleep(2 * (rnd + 1))
        still = []
        for src, dst in failed:
            if safe_copy(src, dst):
                max_sz = max(max_sz, dst.stat().st_size)
            else:
                still.append((src, dst))
        print(f"      补重试第 {rnd+1} 轮，剩余 {len(still)} 个")
        failed = still

    total_mb = sum(f.stat().st_size for f in out_dir.iterdir()) / 1048576
    max_mb = max_sz / 1048576
    print(f"  [{category}] 完成 {len(prepared)} 个文件，{total_mb:.0f} MB，"
          f"单文件最大 {max_mb:.2f} MB，耗时 {time.time()-t0:.1f}s")

    if max_mb > CF_MAX_FILE_MB:
        print(f"    ⚠ 单文件 {max_mb:.1f}MB 超过 Pages {CF_MAX_FILE_MB}MB 上限")
    actual = len(list(out_dir.iterdir()))
    if actual > len(prepared):
        print(f"    ⚠ 目录里有 {actual} 个文件，多于 {len(prepared)}，"
              f"多出 {actual-len(prepared)} 个是旧版残留，请手动清理")
    if failed:
        print(f"    ⚠ 仍有 {len(failed)} 个写入失败（被杀软/索引占用），建议关闭实时扫描后重跑")
    return len(prepared)


def main():
    global REUSE_PREPARED, MAX_WIDTH, JPEG_QUALITY

    ap = argparse.ArgumentParser(description="生成 Cloudflare Pages 随机图片 API 资源")
    ap.add_argument("--only", nargs="*", help="只处理指定分类")
    ap.add_argument("--max-width", type=int, default=MAX_WIDTH)
    ap.add_argument("--quality", type=int, default=JPEG_QUALITY)
    ap.add_argument("--reuse", action="store_true", help="复用 .tmp 预处理结果")
    args = ap.parse_args()

    MAX_WIDTH, JPEG_QUALITY, REUSE_PREPARED = args.max_width, args.quality, args.reuse

    if not SOURCE_DIR.exists():
        print(f"错误：找不到源目录 {SOURCE_DIR}")
        return 1
    ensure_dir(OUTPUT_DIR)

    all_cats = sorted(d for d in SOURCE_DIR.iterdir() if d.is_dir())
    cats = all_cats
    if args.only:
        cats = [d for d in all_cats if d.name in set(args.only)]
        miss = set(args.only) - {d.name for d in cats}
        if miss:
            print(f"警告：oriImg 下没有分类 {miss}")
    if not cats:
        print("没有可处理的分类")
        return 1

    print(f"分类: {[d.name for d in cats]}   压缩: {MAX_WIDTH}px q{JPEG_QUALITY}\n")

    # 清理 dist 中已不存在于 oriImg 的分类（依据 oriImg 全部分类，不受 --only 影响）
    valid = {d.name for d in all_cats}
    for d in list(OUTPUT_DIR.iterdir()):
        if d.is_dir() and d.name not in valid:
            print(f"  清理过期分类: dist/{d.name}")
            shutil.rmtree(d, ignore_errors=True)

    counts = {}
    for d in cats:
        imgs = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXT)
        if imgs:
            n = process_category(d.name, imgs)
            if n:
                counts[d.name] = n
        else:
            print(f"  [{d.name}] 无图片，跳过")

    if counts:
        write_functions(counts)
        write_index(counts)

    total = sum(counts.values())
    print(f"\n完成，共 {total} 个文件（文件数 = 素材数，无重复填充）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
