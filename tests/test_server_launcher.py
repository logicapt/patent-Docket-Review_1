"""Check duplicate starts and alternate ports without touching the user's server."""
import io
import socket
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app
from core.server_launcher import launch, _existing_instance


class LauncherTests(unittest.TestCase):
    def occupied(self, existing):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            output = io.StringIO()
            with patch("core.server_launcher._existing_instance", return_value=existing), patch("core.server_launcher.uvicorn.Server") as server, redirect_stdout(output):
                code = launch(app.app, ["--port", str(port)])
            server.assert_not_called()
            return code, output.getvalue()

    def test_duplicate_start_reuses_app_without_binding_again(self):
        code, output = self.occupied(True)
        self.assertEqual(code, 0)
        self.assertIn("already running", output)

    def test_unrelated_process_requires_alternate_port(self):
        code, output = self.occupied(False)
        self.assertEqual(code, 1)
        self.assertIn("another process", output)
        self.assertIn("--port 5001", output)

    def test_free_port_is_bound_before_uvicorn_and_closed_after_run(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        captured = []
        def run(*, sockets):
            self.assertEqual(sockets[0].getsockname(), ("127.0.0.1", port))
            captured.extend(sockets)
        with patch("core.server_launcher.uvicorn.Server") as server, redirect_stdout(io.StringIO()):
            server.return_value.run.side_effect = run
            self.assertEqual(launch(app.app, ["--port", str(port)]), 0)
        self.assertEqual(captured[0].fileno(), -1)

    def test_health_check_requires_service_identity(self):
        for payload, expected in ((b'{"service":"patent-docket-review","status":"ok"}', True),
                                  (b'{"service":"other-app","status":"ok"}', False), (b'[]', False)):
            response = Mock()
            response.geturl.return_value = "http://127.0.0.1:5000/api/health"
            response.read.return_value = payload
            with patch("core.server_launcher.build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value = response
                self.assertEqual(_existing_instance(5000), expected)
        self.assertEqual(TestClient(app.app).get("/api/health").json(), {"service": "patent-docket-review", "status": "ok"})


if __name__ == "__main__":
    unittest.main()
