from .prompts import PROMPTS, get_prompt
from .protocol import Server
from .tools import HANDLERS, RESOURCES, TOOLS


def main() -> None:
    Server(TOOLS, HANDLERS, RESOURCES, PROMPTS, get_prompt).serve()


if __name__ == "__main__":
    main()
