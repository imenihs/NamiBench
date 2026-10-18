"""QR コードを作る（設計A §4.3.2 の「接続のしかた」: URL を QR にしてスマホで開く）。

パッケージを足さないために、要る分だけを自前で持つ（一般に公開されている QR コードの規格の手順どおり）:
- 符号化はバイトモードだけ（URL を入れるため）。誤り訂正は L と M、版（大きさ）は 1〜10
- 誤り訂正は GF(256)（原始多項式 0x11D）のリード・ソロモン
- マスクは 8 通りを全部試し、規格の減点（同じ色の並び・2×2 の塊・ファインダーに似た並び・明暗の偏り）の
  いちばん少ないものを選ぶ

戻り値の行列は modules[y][x]（True が黒）。周りの白い余白（4 モジュール）は含まない。
"""
from __future__ import annotations

from typing import Optional

# 誤り訂正のレベルと、形式情報に入れる2ビット
ECL_BITS = {"L": 1, "M": 0}

# 版ごとの 1 ブロックあたりの誤り訂正の語数と、ブロックの数（添字は版。0 は使わない）
_ECC_PER_BLOCK = {
    "L": (None, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18),
    "M": (None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),
}
_NUM_BLOCKS = {
    "L": (None, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4),
    "M": (None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),
}
MAX_VERSION = 10


class QrTooLong(ValueError):
    """版 10 に入らない長さ。"""


# ---------------------------------------------------------------------------
# リード・ソロモン
# ---------------------------------------------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def rs_generator(degree: int) -> list[int]:
    """(x − α^0)(x − α^1)…(x − α^(degree−1)) の係数（最上位の 1 を除いた degree 個）。"""
    g = [1]
    for i in range(degree):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _EXP[i])
        g = nxt
    return g[1:]


def rs_remainder(data: list[int], degree: int) -> list[int]:
    """data を生成多項式で割った余り（誤り訂正の語）。"""
    gen = rs_generator(degree)
    rem = [0] * degree
    for b in data:
        factor = b ^ rem[0]
        rem = rem[1:] + [0]
        for i, c in enumerate(gen):
            rem[i] ^= _gf_mul(c, factor)
    return rem


# ---------------------------------------------------------------------------
# 大きさ・配置
# ---------------------------------------------------------------------------


def size_of(version: int) -> int:
    return version * 4 + 17


def raw_data_modules(version: int) -> int:
    """機能の模様（ファインダー・タイミング・位置合わせ・形式と版の情報）を除いた、データを置けるモジュールの数。"""
    n = (16 * version + 128) * version + 64
    if version >= 2:
        k = version // 7 + 2
        n -= (25 * k - 10) * k - 55
        if version >= 7:
            n -= 36
    return n


def data_codewords(version: int, ecl: str) -> int:
    return raw_data_modules(version) // 8 - _ECC_PER_BLOCK[ecl][version] * _NUM_BLOCKS[ecl][version]


def alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    k = version // 7 + 2
    step = (version * 4 + k * 2 + 1) // (k * 2 - 2) * 2
    out = [6]
    pos = size_of(version) - 7
    rest = []
    for _ in range(k - 1):
        rest.insert(0, pos)
        pos -= step
    return out + rest


def format_bits(ecl: str, mask: int) -> int:
    data = ECL_BITS[ecl] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return version << 12 | rem


def _bit(x: int, i: int) -> bool:
    return (x >> i) & 1 != 0


class _Grid:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = size_of(version)
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.fn[y][x] = True

    def draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = alignment_positions(self.version)
        last = len(pos) - 1
        for i, ay in enumerate(pos):
            for j, ax in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format("L", 0)  # 場所を取っておく（あとでマスクを決めて書き直す）
        if self.version >= 7:
            bits = version_bits(self.version)
            for i in range(18):
                a, b = n - 11 + i % 3, i // 3
                self.set_fn(a, b, _bit(bits, i))
                self.set_fn(b, a, _bit(bits, i))

    def draw_format(self, ecl: str, mask: int) -> None:
        bits = format_bits(ecl, mask)
        n = self.size
        for i in range(6):
            self.set_fn(8, i, _bit(bits, i))
        self.set_fn(8, 7, _bit(bits, 6))
        self.set_fn(8, 8, _bit(bits, 7))
        self.set_fn(7, 8, _bit(bits, 8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, _bit(bits, i))
        for i in range(8):
            self.set_fn(n - 1 - i, 8, _bit(bits, i))
        for i in range(8, 15):
            self.set_fn(8, n - 15 + i, _bit(bits, i))
        self.set_fn(8, n - 8, True)  # 常に黒のモジュール

    def draw_codewords(self, data: list[int]) -> None:
        n = self.size
        i = 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(n):
                y = n - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if not self.fn[y][x] and i < len(data) * 8:
                        self.m[y][x] = _bit(data[i >> 3], 7 - (i & 7))
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and mask_hit(mask, x, y):
                    self.m[y][x] = not self.m[y][x]


def mask_hit(mask: int, x: int, y: int) -> bool:
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (x // 3 + y // 2) % 2 == 0
    if mask == 5:
        return x * y % 2 + x * y % 3 == 0
    if mask == 6:
        return (x * y % 2 + x * y % 3) % 2 == 0
    return ((x + y) % 2 + x * y % 3) % 2 == 0


def penalty(m: list[list[bool]]) -> int:
    """規格の減点（N1=3・N2=3・N3=40・N4=10）。"""
    n = len(m)
    score = 0
    lines = [row for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]
    pat_a = [True, False, True, True, True, False, True, False, False, False, False]
    pat_b = pat_a[::-1]
    for line in lines:
        run = 1
        for i in range(1, n + 1):
            if i < n and line[i] == line[i - 1]:
                run += 1
                continue
            if run >= 5:
                score += 3 + (run - 5)
            run = 1
        for i in range(n - 10):
            seg = line[i:i + 11]
            if seg == pat_a or seg == pat_b:
                score += 40
    for y in range(n - 1):
        for x in range(n - 1):
            c = m[y][x]
            if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(1 for v in row if v) for row in m)
    total = n * n
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += max(0, k) * 10
    return score


# ---------------------------------------------------------------------------
# 符号化
# ---------------------------------------------------------------------------


def _codewords(data: bytes, version: int, ecl: str) -> list[int]:
    cap = data_codewords(version, ecl)
    bits: list[int] = []

    def put(v: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            bits.append((v >> i) & 1)

    put(0b0100, 4)  # バイトモード
    put(len(data), 8 if version <= 9 else 16)
    for b in data:
        put(b, 8)
    put(0, min(4, cap * 8 - len(bits)))
    bits.extend([0] * ((-len(bits)) % 8))
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < cap:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    # ブロックに分けて誤り訂正の語を付け、交互に並べる
    nb = _NUM_BLOCKS[ecl][version]
    ecc_len = _ECC_PER_BLOCK[ecl][version]
    raw = raw_data_modules(version) // 8
    n_short = nb - raw % nb
    short_len = raw // nb
    blocks = []
    k = 0
    for i in range(nb):
        dlen = short_len - ecc_len + (0 if i < n_short else 1)
        dat = words[k:k + dlen]
        k += dlen
        ecc = rs_remainder(dat, ecc_len)
        if i < n_short:
            dat = dat + [0]  # 長さをそろえる詰め物（並べるときに飛ばす）
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= n_short:
                out.append(blk[i])
    return out


def choose_version(n_bytes: int, ecl: str) -> int:
    for v in range(1, MAX_VERSION + 1):
        need = 4 + (8 if v <= 9 else 16) + 8 * n_bytes
        if need <= data_codewords(v, ecl) * 8:
            return v
    raise QrTooLong(f"{n_bytes} バイトは版 {MAX_VERSION} に収まりません")


def encode(text: str, ecl: str = "M", mask: Optional[int] = None) -> list[list[bool]]:
    """文字列（UTF-8 のバイト）を QR コードの行列にする。mask を省くと減点のいちばん少ないものを選ぶ。"""
    data = text.encode("utf-8")
    version = choose_version(len(data), ecl)
    words = _codewords(data, version, ecl)
    best = None
    for msk in ([mask] if mask is not None else range(8)):
        g = _Grid(version)
        g.draw_function_patterns()
        g.draw_codewords(words)
        g.apply_mask(msk)
        g.draw_format(ecl, msk)
        p = penalty(g.m)
        if best is None or p < best[0]:
            best = (p, g.m)
    return best[1]


# ---------------------------------------------------------------------------
# 描く
# ---------------------------------------------------------------------------

QUIET = 4  # 周りの白い余白（モジュール）


def to_svg(m: list[list[bool]], scale: int = 6) -> str:
    """SVG の文字列（黒は1本のパス。白の余白込み）。"""
    n = len(m)
    full = (n + 2 * QUIET) * scale
    parts = []
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                parts.append(f"M{(x + QUIET) * scale} {(y + QUIET) * scale}h{scale}v{scale}h-{scale}z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{full}" height="{full}" viewBox="0 0 {full} {full}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path fill="#000" d="{"".join(parts)}"/></svg>')


def to_text(m: list[list[bool]], unicode_blocks: bool = True) -> str:
    """コンソールに出す文字の QR（黒い文字の背景が黒い端末でも読めるよう、明暗を反転して描く）。

    unicode_blocks: 上下2モジュールを1文字にする（▀▄█）。文字の出せない端末では "##" と "  " の2文字で1モジュール。
    """
    n = len(m)
    q = QUIET // 2
    size = n + 2 * q

    def dark(x: int, y: int) -> bool:
        x -= q
        y -= q
        return 0 <= x < n and 0 <= y < n and m[y][x]

    lines = []
    if unicode_blocks:
        # 白いモジュールを文字で塗る（端末の背景は黒が多いので、白＝塗る・黒＝空白）
        for y in range(0, size, 2):
            row = []
            for x in range(size):
                top = not dark(x, y)
                bot = not dark(x, y + 1) if y + 1 < size else False
                row.append("█" if top and bot else "▀" if top else "▄" if bot else " ")
            lines.append("".join(row))
    else:
        for y in range(size):
            lines.append("".join("  " if dark(x, y) else "##" for x in range(size)))
    return "\n".join(lines)
