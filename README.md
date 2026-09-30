# pyGigaSt

自作スペクトラムアナライザ **GigaSt v5** を PC から操作するアプリです。
スペクトラムアナライザ・トラッキングジェネレータ（TG）・信号源（SG）・NF 測定に対応します。
Qt のデスクトップ画面と、ブラウザから使う Web 画面の両方から、同じ機器を操作できます。

**現在は設計中です。** まだ動くアプリはありません。

## 必要なもの

- GigaSt v5 本体（USB 接続。FTDI の USB シリアル変換を使います）
- FTDI の仮想 COM ポートドライバ
- Python 3.13

## 環境構築

プロジェクトのフォルダに `venv` を作り、必要なパッケージを入れます。

Windows（コマンドプロンプト・PowerShell のどちらでも。PowerShell では先頭に `.\` を付けて `.\setup.bat`）:

```bat
setup.bat
setup.bat C:\path\to\python.exe
```

Linux / macOS:

```sh
./setup.sh
./setup.sh /path/to/python3
```

- Python を指定しないときは、環境変数 `PYGIGAST_PYTHON`、次に `py -3.13`（Windows）、最後に PATH 上の `python` / `python3` を使います
- `virtualenv` が入っていればそれを、無ければ標準の `venv` を使います
- 作った環境の Python は `venv\Scripts\python.exe`（Linux / macOS は `venv/bin/python`）です。有効化（activate）しなくても使えます

## 旧アプリ（GS5.exe）からの移行

- 旧アプリの校正ファイル（`gigast.txt` や、機器ごとの個別補正データ）はそのまま読み込めます
- 旧アプリの設定ファイルと保存形式には対応しません
