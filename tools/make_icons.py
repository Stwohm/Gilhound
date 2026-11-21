"""Regenerate Gilhound icons with system Python and Pillow, a one-off dev tool dependency."""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SIZES = (16, 24, 32, 48, 64, 128, 256)
FAVICON_SIZES = (16, 32, 48)


def clean_coin(source: Image.Image) -> tuple[Image.Image, Image.Image]:
    coin = source.convert("RGBA")
    # The coin is opaque. Drop weak alpha around its rim before measuring the crop.
    alpha = coin.getchannel("A").point(lambda value: value if value >= 128 else 0)
    coin.putalpha(alpha)
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError("The source does not contain an opaque coin.")
    left, top, right, bottom = bounds
    diameter = max(right - left, bottom - top)
    square_left = (left + right - diameter) // 2
    square_top = (top + bottom - diameter) // 2

    def crop(margin: int) -> Image.Image:
        return coin.crop((
            square_left - margin, square_top - margin,
            square_left + diameter + margin, square_top + diameter + margin,
        ))

    return crop(round(diameter * 0.025)), crop(round(diameter * 0.005))


def save_ico(path: Path, frames: dict[int, Image.Image], sizes: tuple[int, ...]) -> None:
    frames[sizes[-1]].save(
        path,
        sizes=[(size, size) for size in sizes],
        append_images=[frames[size] for size in sizes[:-1]],
    )


def main() -> None:
    assets = ROOT / "assets"
    public = ROOT / "ui" / "public"
    public.mkdir(parents=True, exist_ok=True)
    with Image.open(assets / "icon-source.png") as source:
        square, tight_square = clean_coin(source)
    frames = {
        size: (tight_square if size <= 32 else square).resize(
            (size, size), Image.Resampling.LANCZOS
        )
        for size in (*WINDOWS_SIZES, 180, 512)
    }
    save_ico(assets / "gilhound.ico", frames, WINDOWS_SIZES)
    frames[256].save(assets / "icon-256.png")
    frames[512].save(assets / "icon-512.png")
    frames[64].save(public / "favicon.png")
    frames[180].save(public / "apple-touch-icon.png")
    save_ico(public / "favicon.ico", frames, FAVICON_SIZES)


if __name__ == "__main__":
    main()
