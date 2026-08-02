"""Render deterministic screen-like PNG fixtures without reading the display."""

from AppKit import (
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSRectFill,
    NSString,
)

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720


def render_synthetic_screen(
    *,
    title: str,
    body: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> bytes:
    """Render supplied synthetic text into an in-memory PNG."""
    if width < 640 or height < 360:
        raise ValueError("synthetic screen dimensions are too small")
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()
    try:
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.055,
            0.071,
            0.102,
            1.0,
        ).set()
        NSRectFill(NSMakeRect(0, 0, width, height))

        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.10,
            0.14,
            0.20,
            1.0,
        ).set()
        panel_margin = max(16, round(width * 0.025))
        NSRectFill(
            NSMakeRect(
                panel_margin,
                panel_margin,
                width - 2 * panel_margin,
                height - 2 * panel_margin,
            )
        )

        _draw_text(
            title,
            x=width * 0.05625,
            y=height * 0.861,
            size=max(18, width * 0.021875),
            color=NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.40,
                0.78,
                1.0,
                1.0,
            ),
        )
        _draw_text(
            body,
            x=width * 0.071875,
            y=height * 0.319,
            size=max(13, width * 0.015625),
            color=NSColor.colorWithCalibratedWhite_alpha_(0.92, 1.0),
        )
    finally:
        image.unlockFocus()

    representation = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    if representation is None:
        raise RuntimeError("could not render synthetic model fixture")
    data = representation.representationUsingType_properties_(
        NSBitmapImageFileTypePNG,
        {},
    )
    if data is None:
        raise RuntimeError("could not encode synthetic model fixture")
    return bytes(data)


def _draw_text(
    text: str,
    *,
    x: float,
    y: float,
    size: float,
    color: NSColor,
) -> None:
    attributes = {
        NSFontAttributeName: NSFont.monospacedSystemFontOfSize_weight_(size, 0.0),
        NSForegroundColorAttributeName: color,
    }
    NSString.stringWithString_(text).drawAtPoint_withAttributes_((x, y), attributes)
