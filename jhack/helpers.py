import contextlib
import json
import json as jsn
import os
import subprocess
import tempfile
from pathlib import Path
from subprocess import PIPE
from typing import List

from juju.model import Model

from jhack.config import IS_SNAPPED
from jhack.logger import logger


def get_models():
    cmd = f"juju models --format json"
    proc = JPopen(cmd.split())
    proc.wait()
    data = json.loads(proc.stdout.read().decode("utf-8"))
    return data


@contextlib.asynccontextmanager
async def get_current_model() -> Model:
    model = Model()
    try:
        # connect to the current model with the current user, per the Juju CLI
        await model.connect()
        yield model

    finally:
        if model.is_connected():
            print("Disconnecting from model")
            await model.disconnect()


def get_local_charm() -> Path:
    cwd = Path(os.getcwd())
    try:
        return next(cwd.glob("*.charm"))
    except StopIteration:
        raise FileNotFoundError(f"could not find a .charm file in {cwd}")


# Env-passing-down Popen
def JPopen(*args, wait=False, **kwargs):
    proc = subprocess.Popen(
        *args,
        env=kwargs.pop("env", os.environ),
        stderr=kwargs.pop("stderr", PIPE),
        stdout=kwargs.pop("stdout", PIPE),
        **kwargs,
    )
    if wait:
        proc.wait()

    # this will presumably only ever branch if wait==True
    if proc.returncode not in {0, None}:
        msg = f"failed to invoke juju command ({args}, {kwargs})"
        if IS_SNAPPED and "ssh client keys" in proc.stderr.read().decode("utf-8"):
            msg += (
                " If you see an ERROR above saying something like "
                "'open ~/.local/share/juju/ssh: permission denied',"
                "you might have forgotten to "
                "'sudo snap connect jhack:dot-local-share-juju snapd'"
            )
        logger.error(msg)

    return proc


def juju_version():
    proc = JPopen("juju version".split())
    raw = proc.stdout.read().decode("utf-8").strip()
    if "-" in raw:
        return raw.split("-")[0]
    return raw


def juju_status(app_name=None, model: str = None, json: bool = False):
    cmd = f'juju status{" " + app_name if app_name else ""} --relations'
    if model:
        cmd += f" -m {model}"
    if json:
        cmd += " --format json"
    proc = JPopen(cmd.split(), stderr=PIPE)
    raw = proc.stdout.read().decode("utf-8")
    if json:
        return jsn.loads(raw)
    return raw


def is_k8s_model(status=None):
    status = status or juju_status(json=True)
    if status["applications"]:
        # no machines = k8s model
        if not status.get("machines"):
            return True
        else:
            return False

    cloud_name = status["model"]["cloud"]
    logger.warning(
        "unable to determine with certainty if the current model is a k8s model or not;"
        f"guessing it based on the cloud name ({cloud_name})"
    )
    return "k8s" in cloud_name


def juju_models() -> str:
    proc = JPopen(f"juju models".split())
    return proc.stdout.read().decode("utf-8")


def show_unit(unit: str):
    proc = JPopen(f"juju show-unit {unit} --format json".split())
    raw = json.loads(proc.stdout.read().decode("utf-8"))
    return raw[unit]


def list_models(strip_star=False) -> List[str]:
    raw = juju_models()
    lines = raw.split("\n")[3:]
    models = filter(None, (line.split(" ")[0] for line in lines))
    if strip_star:
        return [name.strip("*") for name in models]
    return models


def current_model() -> str:
    all_models = list_models()
    key = lambda name: name.endswith("*")
    return next(filter(key, all_models)).strip("*")


@contextlib.contextmanager
def modify_remote_file(unit: str, path: str):
    # need to create tf in ~ else juju>3.0 scp will break (strict snap)
    with tempfile.NamedTemporaryFile(dir=Path("~").expanduser()) as tf:
        # print(f'fetching remote {path}...')

        cmd = [
            "juju",
            "ssh",
            unit,
            "cat",
            path,
        ]
        buf = subprocess.check_output(cmd)
        f = Path(tf.name)
        f.write_bytes(buf)

        yield f

        # print(f'copying back modified {path}...')
        cmd = [
            "juju",
            "scp",
            tf.name,
            f"{unit}:{path}",
        ]
        subprocess.check_call(cmd)
