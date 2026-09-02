"""Small, dependency-free looks built from common ffmpeg filters."""


def fit(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def look(strength: str = "normal") -> str:
    if strength == "night":
        curves = (
            "curves=r='0/0.02 0.5/0.48 1/0.97':"
            "g='0/0.015 0.5/0.5 1/0.98':b='0/0.05 0.5/0.54 1/1'"
        )
        eq = "eq=contrast=1.10:saturation=1.06:gamma=0.98"
    elif strength == "warm":
        curves = (
            "curves=r='0/0.03 0.5/0.54 1/1':"
            "g='0/0.02 0.5/0.5 1/0.98':b='0/0.04 0.5/0.47 1/0.95'"
        )
        eq = "eq=contrast=1.06:saturation=1.10:gamma=1.01"
    else:
        curves = (
            "curves=r='0/0.02 0.5/0.52 1/0.99':"
            "g='0/0.02 0.5/0.5 1/0.99':b='0/0.03 0.5/0.49 1/0.97'"
        )
        eq = "eq=contrast=1.07:saturation=1.08"
    return f"{curves},{eq},vignette=PI/5,noise=alls=6:allf=t+u"
