"""Create or enter the lab-local virtualenv before any OCI code runs."""

import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys


SERVICE_USER = "fsdrsvc"


def ensure_service_user(script_path):
    """Re-exec as the FSDR service account before touching lab state or venv."""
    try:
        service = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        print("Usuário de serviço {} não existe; execute primeiro o setup do FSDR.".format(SERVICE_USER),
              file=sys.stderr)
        return False
    if os.geteuid() == service.pw_uid:
        return True
    sudo = shutil.which("sudo")
    if not sudo:
        print("O lab precisa executar como {}; sudo não está disponível.".format(SERVICE_USER), file=sys.stderr)
        return False
    print("Executando o laboratório como {}...".format(SERVICE_USER), file=sys.stderr)
    try:
        os.execv(sudo, [sudo, "-u", SERVICE_USER, "--", sys.executable,
                        str(Path(script_path).resolve()), *sys.argv[1:]])
    except OSError as exc:
        print("Não foi possível iniciar como {}: {}".format(SERVICE_USER, exc), file=sys.stderr)
        return False


def ensure_virtualenv(script_path):
    root = Path(__file__).resolve().parent.parent
    venv = root / ".venv"
    python = venv / "bin" / "python"
    if Path(sys.prefix).resolve() == venv.resolve():
        return True
    if python.is_file():
        os.execv(str(python), [str(python), str(Path(script_path).resolve()), *sys.argv[1:]])
    print("Virtualenv do laboratório não encontrado em {}.".format(venv), file=sys.stderr)
    if not sys.stdin.isatty():
        print("Não é possível executar sem o virtualenv. Execute em um terminal interativo para criá-lo.",
              file=sys.stderr)
        return False
    answer = input("Criar o virtualenv e instalar os requisitos agora? [s/N] ").strip().lower()
    if answer not in ("s", "sim", "y", "yes"):
        print("Não é possível executar o laboratório sem o virtualenv. Crie-o e execute novamente.",
              file=sys.stderr)
        return False
    try:
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
        subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")])
    except (OSError, subprocess.CalledProcessError) as exc:
        print("Não foi possível criar o virtualenv: {}".format(exc), file=sys.stderr)
        return False
    os.execv(str(python), [str(python), str(Path(script_path).resolve()), *sys.argv[1:]])
