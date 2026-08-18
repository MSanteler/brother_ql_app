"""
Default settings for the Brother QL Printer App.
These settings are used when no user-defined settings are available.
"""

DEFAULT_SETTINGS = {
    "printer_uri": "tcp://192.168.1.100",
    "printer_model": "QL-800",
    "label_size": "62",
    "font_size": 50,
    # Typeface for rendered text. An empty family means "whatever the font
    # catalog defaults to", which is DejaVu Sans -- the face this app hardcoded
    # before fonts were selectable, so an existing install renders unchanged.
    # Style is bold for the same reason: the hardcoded face was DejaVuSans-Bold.
    "font_family": "",
    "font_style": "bold",  # regular | bold | italic | bold_italic
    "alignment": "left",
    "vertical_alignment": "top",  # top | middle | bottom (die-cut only)
    "rotate_mode": "image",  # image = rotate the render | layout = lay out lengthwise
    # How font_size is treated. actual = honour it exactly (content may crop);
    # fit = shrink to fit; custom = font_size * scale_percent%.
    #
    # Defaults to "actual" deliberately: the previous behaviour re-derived
    # font_size whenever auto_fit was on, which was always -- it defaulted True
    # and was not listed here, so a user who set a size rarely got it.
    "scale_mode": "actual",
    "scale_percent": 100,
    # Canva browsing. The OAuth credentials deliberately live in the broker
    # service, not here: refresh tokens are single-use and rotate, so exactly one
    # process may own them. This app only asks that service for a short-lived
    # access token. Empty url = Canva browsing switched off.
    "canva_broker_url": "",
    "canva_broker_token": "",
    "rotate": 0,
    "threshold": 70.0,
    "dither": False,
    "compress": False,
    "red": False,
    "copies": 1,
    "cut_mode": "each",  # each | end | none
    "dpi_600": False,
    "hq": True,
    "keep_alive_enabled": False,
    "keep_alive_interval": 60,  # seconds
    "keep_alive_mode": "forever",  # forever | timed
    "keep_alive_duration_seconds": 7200,  # used when mode == "timed" (default 2h)
    "ipp_port": 631,  # IPP port for network status/keep-alive (IANA standard)
    "printers": [
        {
            "id": "default",
            "name": "Default Printer",
            "printer_uri": "tcp://192.168.1.100",
            "printer_model": "QL-800",
            "label_size": "62"
        }
    ]
}
