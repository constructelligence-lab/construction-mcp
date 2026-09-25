from .protocol import Server
from .tools import HANDLERS, TOOLS


def main() -> None:
    Server(TOOLS, HANDLERS).serve()


if __name__ == "__main__":
    main()
