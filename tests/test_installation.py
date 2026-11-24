"""Static checks for the self-contained Podman installation workflow."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PodmanInstallationTests(unittest.TestCase):
    def test_installer_and_helpers_are_valid_shell(self):
        for relative in (
            "scripts/install-podman.sh",
            "scripts/fsdr-build-image.sh",
            "scripts/fsdr-job.sh",
        ):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / relative)], text=True,
                capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_podman_installation_keeps_runtime_outside_checkout(self):
        installer = (ROOT / "scripts/install-podman.sh").read_text()
        self.assertIn("/opt/oci-fsdr-opensearch", installer)
        self.assertIn("The source checkout can now be removed.", installer)
        self.assertIn("podman python3 curl openssl ca-certificates", installer)
        self.assertIn('install -m 0755 "$SOURCE_ROOT/scripts/fsdr-job.sh"', installer)
        self.assertIn('podman run --rm "${container_options[@]}" "$image" bootstrap', installer)
        bootstrap = installer.index('podman run --rm "${container_options[@]}" "$image" bootstrap')
        self.assertLess(
            bootstrap,
            installer.index('podman rm --force oci-opensearch-fsdr', bootstrap))
        self.assertIn("--restart=always", installer)
        self.assertIn('"$FSDR_HOME/runtime/state:/var/lib/oci-opensearch-fsdr:rw,Z"', installer)
        self.assertIn('chmod 0711 "$FSDR_HOME" "$FSDR_HOME/bin" "$FSDR_HOME/runtime"', installer)
        self.assertNotIn("podman-compose", installer)

    def test_update_preserves_configuration_and_state(self):
        installer = (ROOT / "scripts/install-podman.sh").read_text()
        documentation = (ROOT / "docs/CONTAINER.rst").read_text()
        self.assertIn("--update", installer)
        self.assertIn("Keeping existing configuration", installer)
        self.assertIn("--replace-config", installer)
        self.assertIn("Update an existing installation", documentation)
        self.assertIn("It deliberately preserves", documentation)

    def test_uninstall_removes_only_the_fsdr_deployment(self):
        installer = (ROOT / "scripts/install-podman.sh").read_text()
        documentation = (ROOT / "docs/INSTALL.rst").read_text()
        self.assertIn("--uninstall", installer)
        self.assertIn('podman rm --force oci-opensearch-fsdr', installer)
        self.assertIn('podman image rm --force localhost/oci-opensearch-fsdr:local', installer)
        self.assertIn('rm -rf -- "$FSDR_HOME"', installer)
        self.assertIn("Podman and host dependencies were kept", installer)
        self.assertIn("Desinstalação para testes limpos", documentation)


if __name__ == "__main__":
    unittest.main()
