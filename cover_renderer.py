"""Cover rendering independent of Telegram. Original style rendering is preserved at defaults."""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
from cover_settings import normalize, DEFAULTS
from styles import style_vevo, style_explicit
from styles.explicit_style import radial_spin_blur

ASSETS = Path(__file__).resolve().parent
CANVAS = (1920,1080)


def render(image, settings):
    s = normalize(settings)
    image = ImageOps.exif_transpose(image).convert("RGB")
    for key, enhancer in (("brightness",ImageEnhance.Brightness), ("contrast",ImageEnhance.Contrast),
                          ("saturation",ImageEnhance.Color), ("sharpness",ImageEnhance.Sharpness)):
        if s[key] != 100:
            image = enhancer(image).enhance(s[key]/100)
    # Exact legacy composition at original settings, including its SCREEN blend.
    layout_keys = ("fit", "focus_x", "focus_y", "background", "background_color",
                   "background_brightness", "foreground_x", "foreground_y",
                   "watermark_enabled", "watermark_position", "watermark_opacity", "watermark_margin")
    if all(s[key] == DEFAULTS[key] for key in layout_keys) and s["explicit_blur"] >= 5:
        if s["style"] == "explicit":
            result = style_explicit(image, s["explicit_wm_size"], s["explicit_blur"],
                                    s["explicit_fg_size"], s["explicit_quality"])
        else:
            result = style_vevo(image, s["vevo_wm_size"])
        result = result.convert("RGB")
        target = tuple(map(int,s["resolution"].split("x")))
        return result if target == CANVAS else result.resize(target,Image.Resampling.LANCZOS)
    center = (s["focus_x"]/100, s["focus_y"]/100)
    bg = ImageOps.fit(image, CANVAS, Image.Resampling.LANCZOS, centering=center)
    if s["style"] == "explicit":
        if s["background"] == "solid":
            bg = Image.new("RGB", CANVAS, s["background_color"])
        elif s["explicit_blur"]:
            if s["background"] == "spin":
                bg = radial_spin_blur(bg, min(25,max(5,s["explicit_blur"])))
            else:
                bg = bg.filter(ImageFilter.GaussianBlur(s["explicit_blur"]))
        bg = ImageEnhance.Brightness(bg).enhance(s["background_brightness"]/100).convert("RGBA")
        size = s["explicit_fg_size"]
        fg = ImageOps.fit(image,(size,size),Image.Resampling.LANCZOS,centering=center).convert("RGBA")
        bg.alpha_composite(fg, (round((1920-size)*s["foreground_x"]/100), round((1080-size)*s["foreground_y"]/100)))
    else:
        if s["fit"] == "contain":
            fg = ImageOps.contain(image,CANVAS,Image.Resampling.LANCZOS)
            bg = Image.new("RGB",CANVAS,s["background_color"])
            bg.paste(fg, (round((1920-fg.width)*center[0]),round((1080-fg.height)*center[1])))
        bg = bg.convert("RGBA")
    if s["watermark_enabled"] == "on" and s["watermark_opacity"]:
        explicit = s["style"] == "explicit"
        with Image.open(ASSETS / ("explicit_wm.png" if explicit else "watermark.png")) as asset:
            wm = asset.convert("RGBA")
        size = s["explicit_wm_size" if explicit else "vevo_wm_size"]
        if wm.width > size:
            wm = wm.resize((size,max(1,int(wm.height*size/wm.width))),Image.Resampling.LANCZOS)
        margin = s["watermark_margin"]
        position = s["watermark_position"]
        x = 1920-wm.width-margin if position.endswith("right") else margin
        y = margin if position.startswith("top") else 1080-wm.height-margin
        if position == "center":
            x,y = (1920-wm.width)//2, (1080-wm.height)//2
        if explicit:
            layer = Image.new("RGBA",CANVAS)
            layer.paste(wm,(x,y),wm)
            screened = ImageChops.screen(bg,layer)
            bg = Image.blend(bg,screened,s["watermark_opacity"]/100)
        else:
            wm.putalpha(wm.getchannel("A").point(lambda a: round(a*s["watermark_opacity"]/100)))
            bg.alpha_composite(wm,(x,y))
    target = tuple(map(int,s["resolution"].split("x")))
    result = bg.convert("RGB")
    if target != CANVAS:
        result = result.resize(target,Image.Resampling.LANCZOS)
    return result


def encode(image, settings):
    fmt = settings["export_format"]
    out = BytesIO()
    kwargs = {"quality":settings["jpeg_quality"]} if fmt in ("JPEG","WEBP") else {}
    image.save(out,fmt,**kwargs)
    out.name = settings.get("filename","cover") + "." + {"JPEG":"jpg","PNG":"png","WEBP":"webp"}[fmt]
    out.seek(0)
    return out


def render_bytes(raw, settings):
    with Image.open(BytesIO(raw)) as image:
        if image.width*image.height > 30_000_000:
            raise ValueError("Картинка слишком большая: максимум 30 мегапикселей")
        result = render(image,settings)
    return result, encode(result,settings)
