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

WIDTH = 1280
HEIGHT = 720


def render_synthetic_screen(*, title: str, body: str) -> bytes:
    """Render supplied synthetic text into an in-memory PNG."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(WIDTH, HEIGHT))
    image.lockFocus()
    try:
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.055,
            0.071,
            0.102,
            1.0,
        ).set()
        NSRectFill(NSMakeRect(0, 0, WIDTH, HEIGHT))

        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.10,
            0.14,
            0.20,
            1.0,
        ).set()
        NSRectFill(NSMakeRect(32, 32, WIDTH - 64, HEIGHT - 64))

        _draw_text(
            title,
            x=72,
            y=620,
            size=28,
            color=NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.40,
                0.78,
                1.0,
                1.0,
            ),
        )
        _draw_text(
            body,
            x=92,
            y=230,
            size=20,
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
