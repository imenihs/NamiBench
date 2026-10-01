"""QR コードの符号化（api/web/qr.py: 設計A §4.3.2 の「接続のしかた」の QR）。

QR の読み取りの部品が入っていないので、(1) 規格の解説で広く使われている既知の値（形式情報・版の情報・
リード・ソロモンの誤り訂正の語）と比べ、(2) ここに書いた小さな読み取り（形式情報を読み、マスクを外し、
ジグザグに読み、ブロックを戻し、誤り訂正の語が合うかを確かめ、バイトを取り出す）で元の文字列に戻るかを見る。
"""
import random
import unittest

from pygigast.api.web import qr


def _bits(v: int, n: int) -> str:
    return format(v, f"0{n}b")


def decode(m: list[list[bool]]) -> str:
    """qr.encode の出力を読み戻す（バイトモード・L/M・版 1〜10 だけ）。"""
    n = len(m)
    version = (n - 17) // 4
    # 形式情報（左上の写し）
    bits = 0
    coords = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    for i, (x, y) in enumerate(coords):
        if m[y][x]:
            bits |= 1 << i
    found = None
    for ecl in ("L", "M"):
        for mask in range(8):
            if qr.format_bits(ecl, mask) == bits:
                found = (ecl, mask)
    assert found is not None, "形式情報が読めない"
    ecl, mask = found
    # 2つ目の写しも同じか
    bits2 = 0
    for i in range(8):
        if m[8][n - 1 - i]:
            bits2 |= 1 << i
    for i in range(8, 15):
        if m[n - 15 + i][8]:
            bits2 |= 1 << i
    assert bits2 == bits, "形式情報の2つの写しが違う"
    g = qr._Grid(version)
    g.draw_function_patterns()
    # マスクを外してジグザグに読む
    out_bits = []
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(n):
            y = n - 1 - vert if upward else vert
            for j in range(2):
                x = right - j
                if not g.fn[y][x]:
                    v = m[y][x] ^ qr.mask_hit(mask, x, y)
                    out_bits.append(1 if v else 0)
        right -= 2
    raw = qr.raw_data_modules(version) // 8
    words = [int("".join(map(str, out_bits[i * 8:i * 8 + 8])), 2) for i in range(raw)]
    # ブロックへ戻す
    nb = qr._NUM_BLOCKS[ecl][version]
    ecc_len = qr._ECC_PER_BLOCK[ecl][version]
    n_short = nb - raw % nb
    short_len = raw // nb
    lens = [short_len + (0 if i < n_short else 1) for i in range(nb)]
    k = 0
    # 並べ方: データの部分（短いブロックは short_len-ecc_len 個）を列ごとに、その後に誤り訂正の語を列ごとに
    data_lens = [l - ecc_len for l in lens]
    data_blocks = [[] for _ in range(nb)]
    for i in range(max(data_lens)):
        for j in range(nb):
            if i < data_lens[j]:
                data_blocks[j].append(words[k])
                k += 1
    ecc_blocks = [[] for _ in range(nb)]
    for i in range(ecc_len):
        for j in range(nb):
            ecc_blocks[j].append(words[k])
            k += 1
    data = []
    for d, e in zip(data_blocks, ecc_blocks):
        assert qr.rs_remainder(d, ecc_len) == e, "誤り訂正の語が合わない"
        data += d
    # バイトモードを読む
    s = "".join(_bits(b, 8) for b in data)
    assert s[:4] == "0100", "バイトモードでない"
    cnt_bits = 8 if version <= 9 else 16
    count = int(s[4:4 + cnt_bits], 2)
    pos = 4 + cnt_bits
    payload = bytes(int(s[pos + 8 * i: pos + 8 * i + 8], 2) for i in range(count))
    return payload.decode("utf-8")


class KnownValuesTest(unittest.TestCase):
    def test_format_bits(self):
        # 規格の表の値（誤り訂正 L・マスク 0 と M・マスク 0）
        self.assertEqual(_bits(qr.format_bits("L", 0), 15), "111011111000100")
        self.assertEqual(_bits(qr.format_bits("M", 0), 15), "101010000010010")

    def test_version_bits(self):
        self.assertEqual(_bits(qr.version_bits(7), 18), "000111110010010100")

    def test_reed_solomon_hello_world_1m(self):
        # よく使われる解説の例: "HELLO WORLD" の 1-M のデータの語と誤り訂正の語
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.rs_remainder(data, 10), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_capacity(self):
        # 版 1-M は 16 語・1-L は 19 語・版 10-M は 216 語（規格の表）
        self.assertEqual(qr.data_codewords(1, "M"), 16)
        self.assertEqual(qr.data_codewords(1, "L"), 19)
        self.assertEqual(qr.data_codewords(10, "M"), 216)
        self.assertEqual(qr.alignment_positions(7), [6, 22, 38])
        self.assertEqual(qr.alignment_positions(10), [6, 28, 50])


class RoundTripTest(unittest.TestCase):
    def test_finder_patterns(self):
        m = qr.encode("http://192.168.1.10:8080/#k=" + "A" * 32)
        n = len(m)
        for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
            for y in range(7):
                for x in range(7):
                    d = max(abs(x - 3), abs(y - 3))
                    self.assertEqual(m[oy + y][ox + x], d != 2, (ox, oy, x, y))

    def test_decode_back_all_versions(self):
        rnd = random.Random(1)
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:/#.="
        for ecl in ("L", "M"):
            for v in range(1, qr.MAX_VERSION + 1):
                cap = qr.data_codewords(v, ecl) - (3 if v <= 9 else 4)
                text = "".join(rnd.choice(alphabet) for _ in range(cap))
                m = qr.encode(text, ecl)
                self.assertEqual(len(m), qr.size_of(v), (ecl, v))
                self.assertEqual(decode(m), text, (ecl, v))
                for mask in range(8):
                    self.assertEqual(decode(qr.encode(text, ecl, mask)), text, (ecl, v, mask))

    def test_url_with_key(self):
        url = "http://192.168.100.200:65535/#k=" + "x" * 32
        self.assertEqual(decode(qr.encode(url)), url)

    def test_too_long(self):
        with self.assertRaises(qr.QrTooLong):
            qr.encode("x" * 300)

    def test_svg_and_text(self):
        m = qr.encode("http://192.168.1.10:8080/")
        svg = qr.to_svg(m, 4)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('fill="#000"', svg)
        t = qr.to_text(m)
        self.assertEqual(len(t.splitlines()), (len(m) + 4 + 1) // 2)
        t2 = qr.to_text(m, unicode_blocks=False)
        self.assertEqual(set(t2) - set("# \n"), set())


if __name__ == "__main__":
    unittest.main()
