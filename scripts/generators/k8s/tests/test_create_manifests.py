"""Exercise aliases through the public manifest-generator CLI."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

GENERATOR = Path(os.environ.get("APP_SIM_GENERATOR_ROOT", Path(__file__).parents[1]))


def generate(tmp_path, config, custom_groups=()):
    shutil.copytree(GENERATOR / "templates", tmp_path / "templates")
    for group in custom_groups:
        shutil.copytree(tmp_path / "templates/services", tmp_path / "templates" / group)
    output = tmp_path / "deployments"
    output.mkdir()
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump(config))
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR / "createManifests.py"),
            "--config",
            str(source),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    files = {f.name: yaml.safe_load(f.read_text()) for f in output.iterdir()}
    return result, files


@pytest.mark.parametrize("kind", ["java", "nodejs", "dotnetcore", "mysql"])
@pytest.mark.parametrize("exposed", [False, True])
def test_aliases_share_original_deployment(tmp_path, kind, exposed):
    group = "databases" if kind == "mysql" else "services"
    config = {
        "type": kind,
        "aliases": ["alternate", "another"],
        "servicePort": 1234,
        "serviceAnnotations": {"example": "present"},
    }
    if exposed:
        config["exposedPort"] = 9090
    result, files = generate(tmp_path, {group: {"original": config}})
    assert result.returncode == 0, result.stderr
    original = files["original-service.yaml"]
    for alias in config["aliases"]:
        expected = {**original, "metadata": {**original["metadata"], "name": alias}}
        assert files[f"{alias}-service.yaml"] == expected
        assert files[f"{alias}-service.yaml"]["spec"]["type"] == "ClusterIP"
    assert sum(x["kind"] == "Deployment" for x in files.values()) == 1
    assert sum(x["kind"] == "ConfigMap" for x in files.values()) == 1
    assert len(files) == 5 + exposed


@pytest.mark.parametrize(
    "aliases",
    [
        "alias",
        None,
        3,
        {},
        [None],
        [""],
        ["bad.name"],
        ["../escape"],
        ["Upper"],
        ["1start"],
        ["a-"],
        ["a" * 64],
    ],
)
def test_invalid_aliases_fail_before_writes(tmp_path, aliases):
    result, files = generate(
        tmp_path,
        {
            "services": {
                "first": {"type": "java"},
                "last": {"type": "nodejs", "aliases": aliases},
            }
        },
    )
    assert result.returncode != 0
    assert not files


@pytest.mark.parametrize("target", ["first", "last", "last-ext", "duplicate"])
def test_collisions_fail_before_writes(tmp_path, target):
    aliases = [target, target] if target == "duplicate" else [target]
    result, files = generate(
        tmp_path,
        {
            "services": {
                "first": {"type": "java", "aliases": aliases},
                "last": {"type": "nodejs", "exposedPort": 80},
            }
        },
    )
    assert result.returncode != 0
    assert not files


def test_aliases_cannot_collide_across_services_and_databases(tmp_path):
    result, files = generate(
        tmp_path,
        {
            "services": {"service": {"type": "java", "aliases": ["shared"]}},
            "databases": {"database": {"type": "mysql", "aliases": ["shared"]}},
        },
    )
    assert result.returncode != 0
    assert not files


@pytest.mark.parametrize("aliases", [[], ["a"], ["a" * 63], ["a-1"]])
def test_boundary_aliases(tmp_path, aliases):
    result, files = generate(
        tmp_path, {"services": {"original": {"type": "java", "aliases": aliases}}}
    )
    assert result.returncode == 0, result.stderr
    assert len(files) == 3 + len(aliases)


def test_global_aliases_and_local_override(tmp_path):
    result, files = generate(
        tmp_path,
        {
            "global": {"k8s": {"aliases": ["inherited"]}},
            "services": {
                "first": {"type": "java"},
                "last": {"type": "nodejs", "aliases": ["overridden"]},
            },
        },
    )
    assert result.returncode == 0, result.stderr
    assert (
        files["inherited-service.yaml"]["spec"]["selector"]["app.kubernetes.io/name"]
        == "first"
    )
    assert (
        files["overridden-service.yaml"]["spec"]["selector"]["app.kubernetes.io/name"]
        == "last"
    )


@pytest.mark.parametrize("exposed", [False, True])
def test_aliases_do_not_shadow_custom_services(tmp_path, exposed):
    custom = {"type": "java"}
    if exposed:
        custom["exposedPort"] = 9090
    alias = "custom-target-ext" if exposed else "custom-target"
    result, files = generate(
        tmp_path,
        {
            "services": {"original": {"type": "java", "aliases": [alias]}},
            "custom": {"custom-target": custom},
        },
        custom_groups=("custom",),
    )
    assert result.returncode != 0
    assert not files
