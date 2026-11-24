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
        compose = (ROOT / "scripts/compose.podman.yaml").read_text()
        self.assertIn("/opt/oci-opensearch-fsdr", installer)
        self.assertIn("The source checkout can now be removed.", installer)
        self.assertIn("podman podman-compose python3 curl openssl ca-certificates", installer)
        self.assertIn('install -m 0755 "$SOURCE_ROOT/scripts/fsdr-job.sh"', installer)
        self.assertIn('compose run --rm --no-deps fsdr-api bootstrap', installer)
        self.assertNotIn("build:", compose)
        self.assertIn("image: localhost/oci-opensearch-fsdr:local", compose)
        self.assertIn("/opt/oci-opensearch-fsdr/runtime/state", compose)

    def test_update_preserves_configuration_and_state(self):
        installer = (ROOT / "scripts/install-podman.sh").read_text()
        documentation = (ROOT / "docs/CONTAINER.rst").read_text()
        self.assertIn("--update", installer)
        self.assertIn("Keeping existing configuration", installer)
        self.assertIn("--replace-config", installer)
        self.assertIn("Update an existing installation", documentation)
        self.assertIn("It deliberately preserves", documentation)


if __name__ == "__main__":
    unittest.main()
