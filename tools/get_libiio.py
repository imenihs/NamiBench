"""PlutoSDR に使う libiio（Analog Devices 公式 v0.26 の Windows 64 ビット版）を、アプリのフォルダの `libiio\\` に置く。

setup.bat から venv の Python で呼ぶ。公式の Windows.zip を取り、SHA-256 を確かめてから、`Windows-VS-2022-x64/` の
DLL 7 個だけを取り出す（実行ファイル・開発用のファイルは置かない）。`libiio\\libiio.dll` がもうあれば何もしない。
終了コード: 0 = 置いた・もうあった、1 = 置けなかった（取れない・確かめの値が違う・中身が足りない）。
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/analogdevicesinc/libiio/releases/download/v0.26/Windows.zip"
SHA256 = "4ad4a8c6b3f7145922c122dcfc51d693f0d3f9a0054fb2228814ce58c538a6ca"
SUBDIR = "Windows-VS-2022-x64/"
DLLS = ("libiio.dll", "libiio-sharp.dll", "libserialport-0.dll", "libusb-1.0.dll", "libxml2.dll", "msvcp140.dll",
        "vcruntime140.dll")
ROOT = Path(__file__).resolve().parent.parent


def main(dest: Path = ROOT / "libiio", url: str = URL, sha256: str = SHA256) -> int:
    if (dest / "libiio.dll").exists():
        print(f"libiio: already in {dest}")
        return 0
    print(f"libiio: downloading {url}")
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "Windows.zip"
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(zpath, "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
        except OSError as e:
            print(f"libiio: could not download ({e})")
            return 1
        got = hashlib.sha256(zpath.read_bytes()).hexdigest()
        if got != sha256:
            print(f"libiio: the download is not the expected file (SHA-256 {got})")
            return 1
        with zipfile.ZipFile(zpath) as z:
            names = set(z.namelist())
            missing = [n for n in DLLS if SUBDIR + n not in names]
            if missing:
                print(f"libiio: the zip lacks {', '.join(missing)}")
                return 1
            # 一時のフォルダへ全部出してから名前を付け替える（途中で止まっても半端な libiio\ を残さない）
            stage = Path(tmp) / "libiio"
            stage.mkdir()
            for n in DLLS:
                (stage / n).write_bytes(z.read(SUBDIR + n))
        dest.mkdir(parents=True, exist_ok=True)
        for n in DLLS:
            (stage / n).replace(dest / n)
    print(f"libiio: placed {len(DLLS)} DLLs in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
