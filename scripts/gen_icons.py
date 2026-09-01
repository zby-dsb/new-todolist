"""
用 Pillow 直接生成 Android 应用图标（自适应 + 各密度兜底 + 启动画面）。

设计：
- 背景色：取自源图主色 #c0a080（米色），与页面 --bg #f6efe2 同一色系
- 前景：从源图抠出非背景色的内容（勾 + 列表线），透明背景
- 自适应图标：背景纯色 + 前景缩放到 72/108 安全区
- launcher.png：源图整体缩放（带背景色）
- splash.png：源图居中 + 米色背景（3:2 比例，letterbox）

为何不用 @capacitor/assets：它依赖 sharp，sharp 要从 GitHub 下载预编译二进制，
而本机 GitHub 走代理被掐断。改用 Pillow 是 Python 标准做法，无此问题。
"""
import os
import sys
from PIL import Image, ImageColor

ROOT = r"C:\Users\张晨朔\Desktop\new-todolist"
SRC = os.path.join(ROOT, "assets", "icon_source.png")
RES = os.path.join(ROOT, "android", "app", "src", "main", "res")

BG_COLOR = "#c0a080"   # 米色（取自源图主色）
PAGE_BG = "#f6efe2"    # 页面 --bg（启动画面用更浅的米色）

# 密度 → 边长 (px)。mdpi 是基准 1x，48dp 桌面图标。
DENSITIES = {
    "mdpi":    48,
    "hdpi":    72,
    "xhdpi":   96,
    "xxhdpi":  144,
    "xxxhdpi": 192,
}

# Adaptive icon foreground 画布 = 108dp，安全区 = 72dp
FG_CANVAS = {  # density → canvas 边长 (px)
    "mdpi":    108,
    "hdpi":    162,
    "xhdpi":   216,
    "xxhdpi":  324,
    "xxxhdpi": 432,
}

# splash 是 3:2 比例（480x320 是常见最小规格）
SPLASH_W, SPLASH_H = 480, 320

def extract_foreground(src_img):
    """
    从源图提取前景：把所有非米色背景的像素抠出来，输出为 RGBA 透明背景图。
    判定：像素与 BG_COLOR 的色差 > 阈值 → 前景；否则视作背景（变透明）。
    前景像素保留原色（勾和列表线是 #e0e0e0 灰白，视觉上对比米色已足够）。
    """
    out = Image.new("RGBA", src_img.size, (0, 0, 0, 0))
    src = src_img.convert("RGBA")
    br, bg, bb = ImageColor.getrgb(BG_COLOR)
    threshold = 40  # 色差阈值（0-441）
    sp = src.load()
    op = out.load()
    for y in range(src.size[1]):
        for x in range(src.size[0]):
            r, g, b, a = sp[x, y]
            if a < 128:
                continue
            # 颜色距离（平方和，避 sqrt 加速）
            dr, dg, db = r - br, g - bg, b - bb
            dist2 = dr * dr + dg * dg + db * db
            if dist2 > threshold * threshold:
                op[x, y] = (r, g, b, a)
    return out

def make_launcher(src_img, size):
    """
    桌面图标（v26 之前的兜底）：源图缩放到 size，留 8% 边距。
    留白让圆形蒙版裁切后仍好看。
    """
    inset = max(2, int(size * 0.08))
    target = size - inset * 2
    fg = src_img.resize((target, target), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), ImageColor.getrgb(BG_COLOR) + (255,))
    out.paste(fg, (inset, inset), fg if fg.mode == "RGBA" else None)
    return out

def make_launcher_round(src_img, size):
    """
    圆形桌面图标（v26 之前的兜底）。
    """
    sq = make_launcher(src_img, size)
    # 用 alpha 做圆形蒙版
    mask = Image.new("L", (size, size), 0)
    m_data = mask.load()
    cx = cy = size / 2
    r = size / 2 - 1
    for y in range(size):
        for x in range(size):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                m_data[x, y] = 255
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(sq, (0, 0), mask)
    return out

def make_foreground(fg_img, canvas_size):
    """
    Adaptive icon foreground：透明画布 108dp，前景 72dp 居中。
    """
    out = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    target = int(canvas_size * 72 / 108)  # 72dp 安全区
    fg_resized = fg_img.resize((target, target), Image.LANCZOS)
    off = (canvas_size - target) // 2
    out.paste(fg_resized, (off, off), fg_resized)
    return out

def make_splash(src_img, w, h):
    """
    启动画面：源图按比例缩放居中，米色背景。
    """
    bg = Image.new("RGB", (w, h), ImageColor.getrgb(PAGE_BG))
    # 源图缩放成 height 等高的方形（保留宽高比）
    side = int(h * 0.7)  # 留 30% 边距
    icon = src_img.resize((side, side), Image.LANCZOS).convert("RGBA")
    off_x = (w - side) // 2
    off_y = (h - side) // 2
    bg.paste(icon, (off_x, off_y), icon)
    return bg

def main():
    if not os.path.exists(SRC):
        print(f"错误：源图不存在 {SRC}")
        sys.exit(1)

    src = Image.open(SRC)
    print(f"源图: {src.size} {src.mode}")

    fg = extract_foreground(src)
    print(f"前景提取完成（透明背景，仅勾+列表线）")

    # 1. 桌面图标（5 个密度 + round 变体）
    for density, size in DENSITIES.items():
        d = os.path.join(RES, f"mipmap-{density}")
        os.makedirs(d, exist_ok=True)
        make_launcher(src, size).save(os.path.join(d, "ic_launcher.png"), "PNG")
        make_launcher_round(src, size).save(os.path.join(d, "ic_launcher_round.png"), "PNG")
        # adaptive foreground
        make_foreground(fg, FG_CANVAS[density]).save(
            os.path.join(d, "ic_launcher_foreground.png"), "PNG"
        )
        print(f"  mipmap-{density}: launcher/launcher_round/foreground {size}px")

    # 2. 启动画面（drawable + 5 个 port + 5 个 land）
    splash_targets = [
        "drawable",
        "drawable-port-mdpi", "drawable-port-hdpi", "drawable-port-xhdpi",
        "drawable-port-xxhdpi", "drawable-port-xxxhdpi",
        "drawable-land-mdpi", "drawable-land-hdpi", "drawable-land-xhdpi",
        "drawable-land-xxhdpi", "drawable-land-xxxhdpi",
    ]
    for t in splash_targets:
        d = os.path.join(RES, t)
        os.makedirs(d, exist_ok=True)
        make_splash(src, SPLASH_W, SPLASH_H).save(os.path.join(d, "splash.png"), "PNG")
    print(f"  splash: 11 个目录，{SPLASH_W}x{SPLASH_H}")

    print("✅ 图标生成完成")

if __name__ == "__main__":
    main()
