from .protocol import Server
from .tools import HANDLERS, RESOURCES, TOOLS


def main() -> None:
    Server(TOOLS, HANDLERS, RESOURCES).serve()


if __name__ == "__main__":
    main()
