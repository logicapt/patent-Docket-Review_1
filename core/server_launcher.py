"""Local server startup with explicit port ownership and friendly duplicate handling."""
import argparse
import errno
import json
import socket
import threading
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

import uvicorn


HOST = "127.0.0.1"
SERVICE_ID = "patent-docket-review"


def _port(value):
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Port must be a number from 1 to 65535.")


def _existing_instance(port):
    try:
        # Keep this loopback health check independent of system HTTP proxy settings.
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://{HOST}:{port}/api/health", timeout=2) as response:
            if response.geturl() != f"http://{HOST}:{port}/api/health":
                return False
            data = json.loads(response.read(4096))
            return isinstance(data, dict) and data.get("service") == SERVICE_ID and data.get("status") == "ok"
    except (OSError, URLError, ValueError):
        return False


def _open_when_ready(server, url, finished):
    for _ in range(300):
        if finished.wait(.1):
            return
        if server.started:
            webbrowser.open(url)
            return


def launch(application, argv=None):
    parser = argparse.ArgumentParser(description="Run Patent Docket Review locally.")
    parser.add_argument("--port", type=_port, default=5000, help="Local port (default: 5000)")
    parser.add_argument("--open-browser", action="store_true", help="Open the app when ready")
    args = parser.parse_args(argv)
    url = f"http://{HOST}:{args.port}"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, args.port))
        except OSError as error:
            if error.errno not in {errno.EADDRINUSE, 10048} and getattr(error, "winerror", None) != 10048:
                print(f"Unable to open port {args.port}. Try: python app.py --port 5001")
                return 1
            if _existing_instance(args.port):
                print(f"Patent Docket Review is already running at {url}")
                print("Open that address in your browser. A second server is not needed.")
                if args.open_browser:
                    webbrowser.open(url)
                return 0
            alternative = 5001 if args.port != 5001 else 5002
            print(f"Port {args.port} is already in use by another process.")
            print(f"Use a different port: python app.py --port {alternative}")
            return 1

        # Pass the bound socket to Uvicorn so no other process can claim the port
        # between a preflight check and server startup.
        config = uvicorn.Config(application, host=HOST, port=args.port, access_log=False)
        server = uvicorn.Server(config)
        finished = threading.Event()
        if args.open_browser:
            threading.Thread(target=_open_when_ready, args=(server, url, finished), daemon=True).start()
        print(f"Patent Docket Review: {url}", flush=True)
        print("Keep this terminal open. Press Ctrl+C to stop the server.", flush=True)
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass
        finally:
            finished.set()
        return 0
    finally:
        listener.close()
