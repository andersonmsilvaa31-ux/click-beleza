"""Gera o ZIP de publicação com caminhos POSIX e valida as imagens.

Uso: python scripts/package_release.py [caminho-do-zip]
"""
import re
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".svg": (b"<",),
}
TEXT_SOURCES = {".html", ".css", ".js", ".py"}
ASSET_REFERENCE = re.compile(r"""["'(=]\s*/?(assets/[A-Za-z0-9._/-]+\.(?:jpe?g|png|webp|gif|svg))""", re.I)


def tracked_files():
    output = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"])
    return sorted(item.decode("utf-8") for item in output.split(b"\0") if item)


def validate(files):
    problems = []
    lowered = {}
    for name in files:
        if "\\" in name or name.startswith("/") or ".." in Path(name).parts:
            problems.append(f"Caminho inválido para ZIP portátil: {name}")
        lowered.setdefault(name.casefold(), []).append(name)
    problems += [f"Nomes que só diferem por maiúsculas: {names}" for names in lowered.values() if len(names) > 1]
    available = set(files)
    for name in files:
        path = ROOT / name
        suffix = path.suffix.lower()
        if suffix in SIGNATURES:
            head = path.read_bytes()[:12]
            if not any(head.startswith(sig) for sig in SIGNATURES[suffix]) or (
                suffix == ".webp" and head[8:12] != b"WEBP"
            ):
                problems.append(f"Conteúdo não corresponde à extensão: {name}")
            if path.stat().st_size == 0:
                problems.append(f"Imagem vazia: {name}")
        if suffix in TEXT_SOURCES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for reference in ASSET_REFERENCE.findall(text):
                if reference not in available:
                    problems.append(f"{name} referencia {reference}, que não existe com esse nome exato")
    return problems


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "beauty-agenda.zip"
    files = tracked_files()
    problems = validate(files)
    if problems:
        print("\n".join(problems))
        return 1
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name in files:
            info = ZipInfo.from_file(ROOT / name, arcname=name)
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, (ROOT / name).read_bytes())
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == files
        assert not any("\\" in name for name in archive.namelist())
    print(f"{target}: {len(files)} arquivos, caminhos POSIX e imagens validadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
