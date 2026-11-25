# 文件夹模式，不加壳、不压缩，减少杀毒软件误报。
a = Analysis(
    ["run.pyw"],
    excludes=["tkinter", "unittest", "pydoc", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TouPing",
    icon="assets/touping.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TouPing", upx=False)
