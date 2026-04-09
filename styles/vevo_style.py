from PIL import Image

WATERMARK_PATH = "watermark.png"
TARGET_W, TARGET_H = 1920, 1080


def resize_cover(img, target_w, target_h):
    """Resize image to cover target dimensions"""
    ow, oh = img.size
    orr = ow / oh
    trr = target_w / target_h

    if orr > trr:
        nh = target_h
        nw = int(nh * orr)
    else:
        nw = target_w
        nh = int(nw / orr)

    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - target_w) // 2
    top = (nh - target_h) // 2

    return img.crop((left, top, left + target_w, top + target_h))


def style_vevo(img, wm_size):
    """Apply VEVO style: resize + watermark (high quality)"""
    img = resize_cover(img, TARGET_W, TARGET_H)

    wm = Image.open(WATERMARK_PATH).convert("RGBA")
    if wm.width > wm_size:
        r = wm_size / wm.width
        wm = wm.resize((int(wm.width * r), int(wm.height * r)), Image.LANCZOS)

    img = img.convert("RGBA")
    img.alpha_composite(wm, (30, TARGET_H - wm.height - 30))
    return img
