"""Web entry point: chat in browser instead of terminal (with image support)."""

from prosurface_agent.web import run

if __name__ == "__main__":
    import sys

    port = 8000
    for arg in sys.argv[1:]:
        v = arg.split("=")[-1] if "=" in arg else arg
        try:
            port = int(v)
            break
        except ValueError:
            continue
    run(port)
