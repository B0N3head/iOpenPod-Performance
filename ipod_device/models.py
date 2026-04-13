"""iPod model identification databases — model numbers, USB PIDs, and serial suffixes.

Data tables
~~~~~~~~~~~
- ``IPOD_MODELS``            Model number → (family, gen, capacity, color)
- ``USB_PID_TO_MODEL``       USB Product ID → (family, gen)
- ``IPOD_USB_PIDS``          All known iPod USB Product IDs (frozenset)
- ``SERIAL_LAST3_TO_MODEL``  Serial suffix → model number

Sources
~~~~~~~
- Universal Compendium iPod Models table (universalcompendium.com)
- The Apple Wiki: Models/iPod (theapplewiki.com)
- Linux USB ID Repository
- libgpod ``itdb_device.c``
"""


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  Comprehensive iPod model database                                      ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Maps order number prefixes to (product_line, generation, capacity, color)
#
# Generation naming conventions:
#   The full-size iPod line has TWO numbering systems. This table uses the
#   product-specific generation (matching what users see in "About" screens),
#   with the overall iPod lineage noted in comments.
#
#   Overall iPod gen │ Product-specific gen │ Years │ Apple Model
#   ─────────────────┼──────────────────────┼───────┼────────────
#   1st gen           │ iPod 1st Gen         │ 2001  │ M8541
#   2nd gen           │ iPod 2nd Gen         │ 2002  │ A1019
#   3rd gen           │ iPod 3rd Gen         │ 2003  │ A1040
#   4th gen           │ iPod 4th Gen         │ 2004  │ A1059
#   4th gen (color)   │ iPod Photo           │ 2004  │ A1099
#   5th gen           │ iPod Video 5th Gen   │ 2005  │ A1136
#   5.5th gen         │ iPod Video 5.5th Gen │ 2006  │ A1136 (Rev B)
#   6th gen           │ iPod Classic 1st Gen │ 2007  │ A1238
#   6.5th gen         │ iPod Classic 2nd Gen │ 2008  │ A1238 (Rev A)
#   7th gen           │ iPod Classic 3rd Gen │ 2009  │ A1238 (Rev B/C)

IPOD_MODELS: dict[str, tuple[str, str, str, str]] = {
    # ==========================================================================
    # iPod Classic (2007-2009)
    # ==========================================================================
    'MB029': ("iPod Classic", "1st Gen", "80GB", "Silver"),
    'MB147': ("iPod Classic", "1st Gen", "80GB", "Black"),
    'MB145': ("iPod Classic", "1st Gen", "160GB", "Silver"),
    'MB150': ("iPod Classic", "1st Gen", "160GB", "Black"),
    'MB562': ("iPod Classic", "2nd Gen", "120GB", "Silver"),
    'MB565': ("iPod Classic", "2nd Gen", "120GB", "Black"),
    'MC293': ("iPod Classic", "3rd Gen", "160GB", "Silver"),
    'MC297': ("iPod Classic", "3rd Gen", "160GB", "Black"),

    # ==========================================================================
    # iPod (Scroll Wheel) — 1st Generation (2001)
    # ==========================================================================
    'M8513': ("iPod", "1st Gen", "5GB", "White"),
    'M8541': ("iPod", "1st Gen", "5GB", "White"),
    'M8697': ("iPod", "1st Gen", "5GB", "White"),
    'M8709': ("iPod", "1st Gen", "10GB", "White"),

    # ==========================================================================
    # iPod (Touch Wheel) — 2nd Generation (2002)
    # ==========================================================================
    'M8737': ("iPod", "2nd Gen", "10GB", "White"),
    'M8740': ("iPod", "2nd Gen", "10GB", "White"),
    'M8738': ("iPod", "2nd Gen", "20GB", "White"),
    'M8741': ("iPod", "2nd Gen", "20GB", "White"),

    # ==========================================================================
    # iPod (Dock Connector) — 3rd Generation (2003)
    # ==========================================================================
    'M8976': ("iPod", "3rd Gen", "10GB", "White"),
    'M8946': ("iPod", "3rd Gen", "15GB", "White"),
    'M8948': ("iPod", "3rd Gen", "30GB", "White"),
    'M9244': ("iPod", "3rd Gen", "20GB", "White"),
    'M9245': ("iPod", "3rd Gen", "40GB", "White"),
    'M9460': ("iPod", "3rd Gen", "15GB", "White"),

    # ==========================================================================
    # iPod (Click Wheel) — 4th Generation (2004)
    # ==========================================================================
    'M9268': ("iPod", "4th Gen", "40GB", "White"),
    'M9282': ("iPod", "4th Gen", "20GB", "White"),
    'ME436': ("iPod", "4th Gen", "40GB", "White"),
    'M9787': ("iPod U2", "4th Gen", "20GB", "Black"),

    # ==========================================================================
    # iPod Photo / iPod with color Display — 4th Gen (Color) (2004-2005)
    # ==========================================================================
    'M9585': ("iPod Photo", "4th Gen", "40GB", "White"),
    'M9586': ("iPod Photo", "4th Gen", "60GB", "White"),
    'M9829': ("iPod Photo", "4th Gen", "30GB", "White"),
    'M9830': ("iPod Photo", "4th Gen", "60GB", "White"),
    'MA079': ("iPod Photo", "4th Gen", "20GB", "White"),
    'MA127': ("iPod U2", "4th Gen", "20GB", "Black"),
    'MS492': ("iPod Photo", "4th Gen", "30GB", "White"),
    'MA215': ("iPod Photo", "4th Gen", "20GB", "White"),

    # ==========================================================================
    # iPod Video — 5th Generation (2005)
    # ==========================================================================
    'MA002': ("iPod Video", "5th Gen", "30GB", "White"),
    'MA003': ("iPod Video", "5th Gen", "60GB", "White"),
    'MA146': ("iPod Video", "5th Gen", "30GB", "Black"),
    'MA147': ("iPod Video", "5th Gen", "60GB", "Black"),
    'MA452': ("iPod Video U2", "5th Gen", "30GB", "Black"),

    # ==========================================================================
    # iPod Video — 5.5th Generation / Enhanced (Late 2006)
    # ==========================================================================
    'MA444': ("iPod Video", "5.5th Gen", "30GB", "White"),
    'MA446': ("iPod Video", "5.5th Gen", "30GB", "Black"),
    'MA448': ("iPod Video", "5.5th Gen", "80GB", "White"),
    'MA450': ("iPod Video", "5.5th Gen", "80GB", "Black"),
    'MA664': ("iPod Video U2", "5.5th Gen", "30GB", "Black"),

    # ==========================================================================
    # iPod Mini — 1st Generation (2004)
    # ==========================================================================
    'M9160': ("iPod Mini", "1st Gen", "4GB", "Silver"),
    'M9434': ("iPod Mini", "1st Gen", "4GB", "Green"),
    'M9435': ("iPod Mini", "1st Gen", "4GB", "Pink"),
    'M9436': ("iPod Mini", "1st Gen", "4GB", "Blue"),
    'M9437': ("iPod Mini", "1st Gen", "4GB", "Gold"),

    # ==========================================================================
    # iPod Mini — 2nd Generation (2005)
    # ==========================================================================
    'M9800': ("iPod Mini", "2nd Gen", "4GB", "Silver"),
    'M9801': ("iPod Mini", "2nd Gen", "6GB", "Silver"),
    'M9802': ("iPod Mini", "2nd Gen", "4GB", "Blue"),
    'M9803': ("iPod Mini", "2nd Gen", "6GB", "Blue"),
    'M9804': ("iPod Mini", "2nd Gen", "4GB", "Pink"),
    'M9805': ("iPod Mini", "2nd Gen", "6GB", "Pink"),
    'M9806': ("iPod Mini", "2nd Gen", "4GB", "Green"),
    'M9807': ("iPod Mini", "2nd Gen", "6GB", "Green"),

    # ==========================================================================
    # iPod Nano — 1st Generation (2005)
    # ==========================================================================
    'MA004': ("iPod Nano", "1st Gen", "2GB", "White"),
    'MA005': ("iPod Nano", "1st Gen", "4GB", "White"),
    'MA099': ("iPod Nano", "1st Gen", "2GB", "Black"),
    'MA107': ("iPod Nano", "1st Gen", "4GB", "Black"),
    'MA350': ("iPod Nano", "1st Gen", "1GB", "White"),
    'MA352': ("iPod Nano", "1st Gen", "1GB", "Black"),

    # ==========================================================================
    # iPod Nano — 2nd Generation (2006)
    # ==========================================================================
    'MA426': ("iPod Nano", "2nd Gen", "4GB", "Silver"),
    'MA428': ("iPod Nano", "2nd Gen", "4GB", "Blue"),
    'MA477': ("iPod Nano", "2nd Gen", "2GB", "Silver"),
    'MA487': ("iPod Nano", "2nd Gen", "4GB", "Green"),
    'MA489': ("iPod Nano", "2nd Gen", "4GB", "Pink"),
    'MA497': ("iPod Nano", "2nd Gen", "8GB", "Black"),
    'MA725': ("iPod Nano", "2nd Gen", "4GB", "Red"),
    'MA726': ("iPod Nano", "2nd Gen", "8GB", "Red"),
    'MA899': ("iPod Nano", "2nd Gen", "8GB", "Red"),

    # ==========================================================================
    # iPod Nano — 3rd Generation (2007)
    # ==========================================================================
    'MA978': ("iPod Nano", "3rd Gen", "4GB", "Silver"),
    'MA980': ("iPod Nano", "3rd Gen", "8GB", "Silver"),
    'MB249': ("iPod Nano", "3rd Gen", "8GB", "Blue"),
    'MB253': ("iPod Nano", "3rd Gen", "8GB", "Green"),
    'MB257': ("iPod Nano", "3rd Gen", "8GB", "Red"),
    'MB261': ("iPod Nano", "3rd Gen", "8GB", "Black"),
    'MB453': ("iPod Nano", "3rd Gen", "8GB", "Pink"),

    # ==========================================================================
    # iPod Nano — 4th Generation (2008)
    # ==========================================================================
    'MB480': ("iPod Nano", "4th Gen", "4GB", "Silver"),
    'MB651': ("iPod Nano", "4th Gen", "4GB", "Blue"),
    'MB654': ("iPod Nano", "4th Gen", "4GB", "Pink"),
    'MB657': ("iPod Nano", "4th Gen", "4GB", "Purple"),
    'MB660': ("iPod Nano", "4th Gen", "4GB", "Orange"),
    'MB663': ("iPod Nano", "4th Gen", "4GB", "Green"),
    'MB666': ("iPod Nano", "4th Gen", "4GB", "Yellow"),
    'MB598': ("iPod Nano", "4th Gen", "8GB", "Silver"),
    'MB732': ("iPod Nano", "4th Gen", "8GB", "Blue"),
    'MB735': ("iPod Nano", "4th Gen", "8GB", "Pink"),
    'MB739': ("iPod Nano", "4th Gen", "8GB", "Purple"),
    'MB742': ("iPod Nano", "4th Gen", "8GB", "Orange"),
    'MB745': ("iPod Nano", "4th Gen", "8GB", "Green"),
    'MB748': ("iPod Nano", "4th Gen", "8GB", "Yellow"),
    'MB751': ("iPod Nano", "4th Gen", "8GB", "Red"),
    'MB754': ("iPod Nano", "4th Gen", "8GB", "Black"),
    'MB903': ("iPod Nano", "4th Gen", "16GB", "Silver"),
    'MB905': ("iPod Nano", "4th Gen", "16GB", "Blue"),
    'MB907': ("iPod Nano", "4th Gen", "16GB", "Pink"),
    'MB909': ("iPod Nano", "4th Gen", "16GB", "Purple"),
    'MB911': ("iPod Nano", "4th Gen", "16GB", "Orange"),
    'MB913': ("iPod Nano", "4th Gen", "16GB", "Green"),
    'MB915': ("iPod Nano", "4th Gen", "16GB", "Yellow"),
    'MB917': ("iPod Nano", "4th Gen", "16GB", "Red"),
    'MB918': ("iPod Nano", "4th Gen", "16GB", "Black"),

    # ==========================================================================
    # iPod Nano — 5th Generation (2009)
    # ==========================================================================
    'MC027': ("iPod Nano", "5th Gen", "8GB", "Silver"),
    'MC031': ("iPod Nano", "5th Gen", "8GB", "Black"),
    'MC034': ("iPod Nano", "5th Gen", "8GB", "Purple"),
    'MC037': ("iPod Nano", "5th Gen", "8GB", "Blue"),
    'MC040': ("iPod Nano", "5th Gen", "8GB", "Green"),
    'MC043': ("iPod Nano", "5th Gen", "8GB", "Yellow"),
    'MC046': ("iPod Nano", "5th Gen", "8GB", "Orange"),
    'MC049': ("iPod Nano", "5th Gen", "8GB", "Red"),
    'MC050': ("iPod Nano", "5th Gen", "8GB", "Pink"),
    'MC060': ("iPod Nano", "5th Gen", "16GB", "Silver"),
    'MC062': ("iPod Nano", "5th Gen", "16GB", "Black"),
    'MC064': ("iPod Nano", "5th Gen", "16GB", "Purple"),
    'MC066': ("iPod Nano", "5th Gen", "16GB", "Blue"),
    'MC068': ("iPod Nano", "5th Gen", "16GB", "Green"),
    'MC070': ("iPod Nano", "5th Gen", "16GB", "Yellow"),
    'MC072': ("iPod Nano", "5th Gen", "16GB", "Orange"),
    'MC074': ("iPod Nano", "5th Gen", "16GB", "Red"),
    'MC075': ("iPod Nano", "5th Gen", "16GB", "Pink"),

    # ==========================================================================
    # iPod Nano — 6th Generation (2010)
    # ==========================================================================
    'MC525': ("iPod Nano", "6th Gen", "8GB", "Silver"),
    'MC688': ("iPod Nano", "6th Gen", "8GB", "Graphite"),
    'MC689': ("iPod Nano", "6th Gen", "8GB", "Blue"),
    'MC690': ("iPod Nano", "6th Gen", "8GB", "Green"),
    'MC691': ("iPod Nano", "6th Gen", "8GB", "Orange"),
    'MC692': ("iPod Nano", "6th Gen", "8GB", "Pink"),
    'MC693': ("iPod Nano", "6th Gen", "8GB", "Red"),
    'MC526': ("iPod Nano", "6th Gen", "16GB", "Silver"),
    'MC694': ("iPod Nano", "6th Gen", "16GB", "Graphite"),
    'MC695': ("iPod Nano", "6th Gen", "16GB", "Blue"),
    'MC696': ("iPod Nano", "6th Gen", "16GB", "Green"),
    'MC697': ("iPod Nano", "6th Gen", "16GB", "Orange"),
    'MC698': ("iPod Nano", "6th Gen", "16GB", "Pink"),
    'MC699': ("iPod Nano", "6th Gen", "16GB", "Red"),

    # ==========================================================================
    # iPod Nano — 7th Generation (2012)
    # ==========================================================================
    'MD475': ("iPod Nano", "7th Gen", "16GB", "Pink"),
    'MD476': ("iPod Nano", "7th Gen", "16GB", "Yellow"),
    'MD477': ("iPod Nano", "7th Gen", "16GB", "Blue"),
    'MD478': ("iPod Nano", "7th Gen", "16GB", "Green"),
    'MD479': ("iPod Nano", "7th Gen", "16GB", "Purple"),
    'MD480': ("iPod Nano", "7th Gen", "16GB", "Silver"),
    'MD481': ("iPod Nano", "7th Gen", "16GB", "Slate"),
    'MD744': ("iPod Nano", "7th Gen", "16GB", "Red"),
    'ME971': ("iPod Nano", "7th Gen", "16GB", "Space Gray"),
    'MKMV2': ("iPod Nano", "7th Gen", "16GB", "Pink"),
    'MKMX2': ("iPod Nano", "7th Gen", "16GB", "Gold"),
    'MKN02': ("iPod Nano", "7th Gen", "16GB", "Blue"),
    'MKN22': ("iPod Nano", "7th Gen", "16GB", "Silver"),
    'MKN52': ("iPod Nano", "7th Gen", "16GB", "Space Gray"),
    'MKN72': ("iPod Nano", "7th Gen", "16GB", "Red"),

    # ==========================================================================
    # iPod Shuffle — 1st Generation (2005)
    # ==========================================================================
    'M9724': ("iPod Shuffle", "1st Gen", "512MB", "White"),
    'M9725': ("iPod Shuffle", "1st Gen", "1GB", "White"),

    # ==========================================================================
    # iPod Shuffle — 2nd Generation (2006-2008)
    # ==========================================================================
    'MA546': ("iPod Shuffle", "2nd Gen", "1GB", "Silver"),
    'MA564': ("iPod Shuffle", "2nd Gen", "1GB", "Silver"),
    'MA947': ("iPod Shuffle", "2nd Gen", "1GB", "Pink"),
    'MA949': ("iPod Shuffle", "2nd Gen", "1GB", "Blue"),
    'MA951': ("iPod Shuffle", "2nd Gen", "1GB", "Green"),
    'MA953': ("iPod Shuffle", "2nd Gen", "1GB", "Orange"),
    'MB225': ("iPod Shuffle", "2nd Gen", "1GB", "Silver"),
    'MB227': ("iPod Shuffle", "2nd Gen", "1GB", "Blue"),
    'MB228': ("iPod Shuffle", "2nd Gen", "1GB", "Blue"),
    'MB229': ("iPod Shuffle", "2nd Gen", "1GB", "Green"),
    'MB231': ("iPod Shuffle", "2nd Gen", "1GB", "Red"),
    'MB233': ("iPod Shuffle", "2nd Gen", "1GB", "Purple"),
    'MB518': ("iPod Shuffle", "2nd Gen", "2GB", "Silver"),
    'MB520': ("iPod Shuffle", "2nd Gen", "2GB", "Blue"),
    'MB522': ("iPod Shuffle", "2nd Gen", "2GB", "Green"),
    'MB524': ("iPod Shuffle", "2nd Gen", "2GB", "Red"),
    'MB526': ("iPod Shuffle", "2nd Gen", "2GB", "Purple"),
    'MB811': ("iPod Shuffle", "2nd Gen", "1GB", "Pink"),
    'MB813': ("iPod Shuffle", "2nd Gen", "1GB", "Blue"),
    'MB815': ("iPod Shuffle", "2nd Gen", "1GB", "Green"),
    'MB817': ("iPod Shuffle", "2nd Gen", "1GB", "Red"),
    'MB681': ("iPod Shuffle", "2nd Gen", "2GB", "Pink"),
    'MB683': ("iPod Shuffle", "2nd Gen", "2GB", "Blue"),
    'MB685': ("iPod Shuffle", "2nd Gen", "2GB", "Green"),
    'MB779': ("iPod Shuffle", "2nd Gen", "2GB", "Red"),
    'MC167': ("iPod Shuffle", "2nd Gen", "1GB", "Gold"),

    # ==========================================================================
    # iPod Shuffle — 3rd Generation (2009)
    # ==========================================================================
    'MB867': ("iPod Shuffle", "3rd Gen", "4GB", "Silver"),
    'MC164': ("iPod Shuffle", "3rd Gen", "4GB", "Black"),
    'MC306': ("iPod Shuffle", "3rd Gen", "2GB", "Silver"),
    'MC323': ("iPod Shuffle", "3rd Gen", "2GB", "Black"),
    'MC381': ("iPod Shuffle", "3rd Gen", "2GB", "Green"),
    'MC384': ("iPod Shuffle", "3rd Gen", "2GB", "Blue"),
    'MC387': ("iPod Shuffle", "3rd Gen", "2GB", "Pink"),
    'MC303': ("iPod Shuffle", "3rd Gen", "4GB", "Stainless Steel"),
    'MC307': ("iPod Shuffle", "3rd Gen", "4GB", "Green"),
    'MC328': ("iPod Shuffle", "3rd Gen", "4GB", "Blue"),
    'MC331': ("iPod Shuffle", "3rd Gen", "4GB", "Pink"),

    # ==========================================================================
    # iPod Shuffle — 4th Generation (2010-2015)
    # ==========================================================================
    'MC584': ("iPod Shuffle", "4th Gen", "2GB", "Silver"),
    'MC585': ("iPod Shuffle", "4th Gen", "2GB", "Pink"),
    'MC749': ("iPod Shuffle", "4th Gen", "2GB", "Orange"),
    'MC750': ("iPod Shuffle", "4th Gen", "2GB", "Green"),
    'MC751': ("iPod Shuffle", "4th Gen", "2GB", "Blue"),
    'MD773': ("iPod Shuffle", "4th Gen", "2GB", "Pink"),
    'MD774': ("iPod Shuffle", "4th Gen", "2GB", "Yellow"),
    'MD775': ("iPod Shuffle", "4th Gen", "2GB", "Blue"),
    'MD776': ("iPod Shuffle", "4th Gen", "2GB", "Green"),
    'MD777': ("iPod Shuffle", "4th Gen", "2GB", "Purple"),
    'MD778': ("iPod Shuffle", "4th Gen", "2GB", "Silver"),
    'MD779': ("iPod Shuffle", "4th Gen", "2GB", "Slate"),
    'MD780': ("iPod Shuffle", "4th Gen", "2GB", "Red"),
    'ME949': ("iPod Shuffle", "4th Gen", "2GB", "Space Gray"),
    'MKM72': ("iPod Shuffle", "4th Gen", "2GB", "Pink"),
    'MKM92': ("iPod Shuffle", "4th Gen", "2GB", "Gold"),
    'MKME2': ("iPod Shuffle", "4th Gen", "2GB", "Blue"),
    'MKMG2': ("iPod Shuffle", "4th Gen", "2GB", "Silver"),
    'MKMJ2': ("iPod Shuffle", "4th Gen", "2GB", "Space Gray"),
    'MKML2': ("iPod Shuffle", "4th Gen", "2GB", "Red"),
}


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  USB Product ID → iPod generation (Apple VID = 0x05AC)                  ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

USB_PID_TO_MODEL: dict[int, tuple[str, str]] = {
    # ── Normal-mode PIDs (0x120x) ──────────────────────────────────────────
    0x1201: ("iPod", "3rd Gen"),
    0x1202: ("iPod", "2nd Gen"),
    0x1203: ("iPod", "4th Gen"),
    0x1204: ("iPod Photo", "4th Gen"),
    0x1205: ("iPod Mini", "1st Gen"),
    0x1206: ("iPod Nano", "1st Gen"),
    0x1207: ("iPod Mini", "2nd Gen"),
    0x1208: ("iPod", "1st Gen"),
    0x1209: ("iPod Video", "5th Gen"),
    0x120A: ("iPod Nano", "2nd Gen"),

    # ── DFU / WTF recovery mode PIDs (0x124x) ─────────────────────────────
    0x1240: ("iPod Nano", "2nd Gen (Recovery)"),
    0x1241: ("iPod Classic", "1st Gen (Recovery)"),
    0x1242: ("iPod Nano", "3rd Gen (Recovery)"),
    0x1243: ("iPod Nano", "4th Gen (Recovery)"),
    0x1245: ("iPod Classic", "3rd Gen (Recovery)"),
    0x1246: ("iPod Nano", "5th Gen (Recovery)"),
    0x1255: ("iPod Nano", "4th Gen (Recovery)"),

    # ── Normal-mode PIDs (0x126x) ──────────────────────────────────────────
    0x1260: ("iPod Nano", "2nd Gen"),
    0x1261: ("iPod Classic", ""),
    0x1262: ("iPod Nano", "3rd Gen"),
    0x1263: ("iPod Nano", "4th Gen"),
    0x1265: ("iPod Nano", "5th Gen"),
    0x1266: ("iPod Nano", "6th Gen"),
    0x1267: ("iPod Nano", "7th Gen"),

    # ── iPod Shuffle PIDs ──────────────────────────────────────────────────
    0x1300: ("iPod Shuffle", "1st Gen"),
    0x1301: ("iPod Shuffle", "2nd Gen"),
    0x1302: ("iPod Shuffle", "3rd Gen"),
    0x1303: ("iPod Shuffle", "4th Gen"),
}

IPOD_USB_PIDS: frozenset[int] = frozenset(USB_PID_TO_MODEL)


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  Serial number last-3-char → model number (from libgpod)                ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

SERIAL_LAST3_TO_MODEL: dict[str, str] = {
    # ── iPod Classic ────────────────────────────────────────────────────
    "Y5N": "MB029", "YMV": "MB147", "YMU": "MB145", "YMX": "MB150",
    "2C5": "MB562", "2C7": "MB565",
    "9ZS": "MC293", "9ZU": "MC297",
    # ── iPod 1G (scroll wheel) ─────────────────────────────────────────
    "LG6": "M8541", "NAM": "M8541", "MJ2": "M8541",
    "ML1": "M8709", "MME": "M8709",
    # ── iPod 2G (touch wheel) ──────────────────────────────────────────
    "MMB": "M8737", "MMC": "M8738",
    "NGE": "M8740", "NGH": "M8740", "MMF": "M8741",
    # ── iPod 3G (dock connector) ───────────────────────────────────────
    "NLW": "M8946", "NRH": "M8976", "QQF": "M9460",
    "PQ5": "M9244", "PNT": "M9244", "NLY": "M8948", "NM7": "M8948",
    "PNU": "M9245",
    # ── iPod 4G (click wheel) ──────────────────────────────────────────
    "PS9": "M9282", "Q8U": "M9282", "PQ7": "M9268",
    # ── iPod U2 ────────────────────────────────────────────────────────
    "V9V": "M9787", "S2X": "M9787",
    # ── iPod Photo / Color Display ─────────────────────────────────────
    "TDU": "MA079", "TDS": "MA079", "TM2": "MA127",
    "SAZ": "M9830", "SB1": "M9830", "SAY": "M9829",
    "R5Q": "M9585", "R5R": "M9586", "R5T": "M9586",
    # ── iPod Mini 1G ───────────────────────────────────────────────────
    "PFW": "M9160", "PRC": "M9160",
    "QKL": "M9436", "QKQ": "M9436", "QKK": "M9435", "QKP": "M9435",
    "QKJ": "M9434", "QKN": "M9434", "QKM": "M9437", "QKR": "M9437",
    # ── iPod Mini 2G ───────────────────────────────────────────────────
    "S41": "M9800", "S4C": "M9800", "S43": "M9802", "S45": "M9804", "S4G": "M9804",
    "S47": "M9806", "S4J": "M9806", "S42": "M9801", "S44": "M9803",
    "S48": "M9807",
    # ── Shuffle 1G ─────────────────────────────────────────────────────
    "RS9": "M9724", "QGV": "M9724", "TSX": "M9724", "PFV": "M9724",
    "R80": "M9724", "RSA": "M9725", "TSY": "M9725", "C60": "M9725",
    # ── Shuffle 2G ─────────────────────────────────────────────────────
    "VTE": "MA546", "VTF": "MA546",
    "XQ5": "MA947", "XQS": "MA947", "XQV": "MA949", "XQX": "MA949",
    "YX7": "MB228", "XQY": "MA951", "YX8": "MA951", "XR1": "MA953",
    "YXA": "MB233", "YX6": "MB225", "YX9": "MB225",
    "8CQ": "MC167", "1ZH": "MB518",
    # ── Shuffle 3G ─────────────────────────────────────────────────────
    "A1S": "MC306", "A78": "MC323", "ALB": "MC381", "ALD": "MC384",
    "ALG": "MC387", "4NZ": "MB867", "891": "MC164",
    "A1L": "MC303", "A1U": "MC307", "A7B": "MC328", "A7D": "MC331",
    # ── Shuffle 4G ─────────────────────────────────────────────────────
    "CMJ": "MC584", "CMK": "MC585", "FDM": "MC749", "FDN": "MC750",
    "FDP": "MC751",
    # ── Nano 1G ────────────────────────────────────────────────────────
    "TUZ": "MA004", "TV0": "MA005", "TUY": "MA099", "TV1": "MA107",
    "UYN": "MA350", "UYP": "MA352",
    "UNA": "MA350", "UNB": "MA350", "UPR": "MA352", "UPS": "MA352",
    "SZB": "MA004", "SZV": "MA004", "SZW": "MA004",
    "SZC": "MA005", "SZT": "MA005",
    "TJT": "MA099", "TJU": "MA099", "TK2": "MA107", "TK3": "MA107",
    # ── Nano 2G ────────────────────────────────────────────────────────
    "VQ5": "MA477", "VQ6": "MA477",
    "V8T": "MA426", "V8U": "MA426",
    "V8W": "MA428", "V8X": "MA428",
    "VQH": "MA487", "VQJ": "MA487",
    "VQK": "MA489", "VQL": "MA489", "VKL": "MA489",
    "WL2": "MA725", "WL3": "MA725",
    "X9A": "MA726", "X9B": "MA726",
    "VQT": "MA497", "VQU": "MA497",
    "YER": "MA899", "YES": "MA899",
    # ── Nano 3G ────────────────────────────────────────────────────────
    "Y0P": "MA978", "Y0R": "MA980",
    "YXR": "MB249", "YXV": "MB257", "YXT": "MB253", "YXX": "MB261",
    # ── Nano 4G ────────────────────────────────────────────────────────
    "37P": "MB663", "37Q": "MB666", "37H": "MB654", "1P1": "MB480",
    "37K": "MB657", "37L": "MB660", "2ME": "MB598",
    "3QS": "MB732", "3QT": "MB735", "3QU": "MB739", "3QW": "MB742",
    "3QX": "MB745", "3QY": "MB748", "3R0": "MB754", "3QZ": "MB751",
    "5B7": "MB903", "5B8": "MB905", "5B9": "MB907", "5BA": "MB909",
    "5BB": "MB911", "5BC": "MB913", "5BD": "MB915", "5BE": "MB917",
    "5BF": "MB918",
    # ── Nano 5G ────────────────────────────────────────────────────────
    "71V": "MC027", "71Y": "MC031", "721": "MC034", "726": "MC037",
    "72A": "MC040", "72F": "MC046", "72K": "MC049", "72L": "MC050",
    "72Q": "MC060", "72R": "MC062",
    "72S": "MC064", "72X": "MC066", "734": "MC068", "738": "MC070",
    "739": "MC072", "73A": "MC074", "73B": "MC075",
    # ── Nano 6G ────────────────────────────────────────────────────────
    "CMN": "MC525", "CMP": "MC526",
    "DVX": "MC688", "DVY": "MC689", "DW0": "MC690", "DW1": "MC691",
    "DW2": "MC692", "DW3": "MC693",
    "DW4": "MC694", "DW5": "MC695", "DW6": "MC696", "DW7": "MC697",
    "DW8": "MC698", "DW9": "MC699",
    # ── Video 5G ───────────────────────────────────────────────────────
    "SZ9": "MA002", "WEC": "MA002", "WED": "MA002", "WEG": "MA002",
    "WEH": "MA002", "WEL": "MA002",
    "TXK": "MA146", "TXM": "MA146", "WEF": "MA146",
    "WEJ": "MA146", "WEK": "MA146",
    "SZA": "MA003", "SZU": "MA003", "TXL": "MA147", "TXN": "MA147",
    # ── Video 5.5G ─────────────────────────────────────────────────────
    "V9K": "MA444", "V9L": "MA444", "WU9": "MA444",
    "VQM": "MA446", "V9M": "MA446", "V9N": "MA446", "WEE": "MA446",
    "V9P": "MA448", "V9Q": "MA448",
    "V9R": "MA450", "V9S": "MA450", "V95": "MA450",
    "V96": "MA450", "WUC": "MA450",
    "W9G": "MA664",
}
