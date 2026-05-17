import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4
from zipfile import ZipFile

type Json = dict[str, Any]


def get_template(name: str) -> Json:
    if not name[0].islower():
        name = name[0].lower() + name[1:]
    path = Path(__file__).parent.joinpath("templates").joinpath(name).with_suffix(".json")
    return json.loads(path.read_text())


def format_text(text: str) -> str:
    return "".join(f"<p>{line}</p>" for line in text.splitlines())


def iter_children(path: Path) -> Iterable[Path]:
    for child in path.iterdir():
        if child.is_file():
            yield child
        elif child.is_dir():
            yield from iter_children(child)
        else:
            raise ValueError


def bundle_template(out: Path) -> None:
    if out.exists():
        out.unlink()
    template = Path(__file__).parent / "templates" / "template"
    with ZipFile(out, mode="w", compresslevel=9) as zip:
        for file in iter_children(template):
            zip.write(file, file.relative_to(template))


@dataclass
class Element:
    subcontent_id: UUID = field(default_factory=uuid4, init=False)

    def to_json(self) -> Json:
        return get_template(type(self).__name__)


@dataclass
class OuterElement(Element):
    title: str
    index: int | None = field(default=None, init=False)

    def to_json(self) -> Json:
        data = super().to_json()
        data["type"]["metadata"]["title"] = self.title
        data["type"]["subContentId"] = str(self.subcontent_id)
        return data

    def build_task(self) -> str:
        questions: dict[UUID, OuterElement] = {}
        found: list[OuterElement] = [self]
        while found:
            curr = found.pop()
            questions[curr.subcontent_id] = curr
            if isinstance(curr, Presentation) and curr.next_question is not None:
                found.append(curr.next_question)
            elif isinstance(curr, BranchingQuestion):
                found.extend(alternative.next_question for alternative in curr.alternatives)
        question_list = list(questions.values())
        for i, question in enumerate(question_list):
            question.index = i
        data = get_template("branchingScenario")
        data["branchingScenario"]["content"] = [question.to_json() for question in question_list]
        return json.dumps(data)

    def package_task(self, out: Path) -> None:
        if out.exists():
            out.unlink()
        template = Path(__file__).parent / "templates" / "template.h5p"
        template.copy(out)
        with ZipFile(out, mode="a", compresslevel=9) as zip:
            zip.writestr("content/content.json", self.build_task())


@dataclass
class PresentationElement(Element):
    x: int
    y: int
    width: int
    height: int

    def to_json(self) -> Json:
        data = super().to_json()
        data["subContentId"] = str(self.subcontent_id)
        return data


@dataclass
class Presentation(OuterElement):
    inner_elements: list[PresentationElement] = field(default_factory=list[PresentationElement])
    next_question: OuterElement | None = field(default=None, kw_only=True)

    def to_json(self) -> Json:
        data = super().to_json()
        data["nextContentId"] = self.next_question.index if self.next_question is not None else -1
        data["type"]["params"]["presentation"]["slides"][0]["elements"] = [
            {
                "x": elem.x,
                "y": elem.y,
                "width": elem.width,
                "height": elem.height,
                "action": elem.to_json(),
                "alwaysDisplayComments": False,
                "backgroundOpacity": 0,
                "displayAsButton": False,
                "buttonSize": "big",
                "goToSlideType": "specified",
                "invisible": False,
                "solution": "",
            }
            for elem in self.inner_elements
        ]
        return data


@dataclass
class MultipleChoiceAnswer:
    text: str
    correct: bool

    def to_json(self) -> Json:
        return {
            "correct": self.correct,
            "tipsAndFeedback": {
                "tip": "",
                "chosenFeedback": "",
                "notChosenFeedback": "",
            },
            "text": f"<div>{self.text}</div>",
        }


@dataclass
class MultipleChoiceQuestion(PresentationElement):
    question: str
    answers: list[MultipleChoiceAnswer]

    def to_json(self) -> Json:
        data = super().to_json()
        data["params"]["question"] = format_text(self.question)
        data["params"]["answers"] = [answer.to_json() for answer in self.answers]
        return data


@dataclass
class Blanks(PresentationElement):
    description: str
    top_text: str
    bottom_text: str
    answers: list[str]

    def to_json(self) -> Json:
        data = get_template("blanks")
        data["params"]["text"] = format_text(self.description)
        data["params"]["questions"][0] = (
            format_text(self.top_text) + format_text("*" + "/".join(self.answers) + "*") + format_text(self.bottom_text)
        )
        return data


@dataclass
class Text(PresentationElement):
    text: str

    def to_json(self) -> dict[str, Any]:
        data = super().to_json()
        data["params"]["text"] = format_text(self.text)
        return data


@dataclass
class BranchingAlternative:
    text: str
    next_question: OuterElement

    def to_json(self) -> Json:
        return {
            "nextContentId": self.next_question.index,
            "feedback": {
                "title": "",
                "subtitle": "",
            },
            "text": self.text,
        }


@dataclass
class BranchingQuestion(OuterElement):
    question: str
    alternatives: list[BranchingAlternative]

    template: ClassVar = "branchingQuestion"

    def to_json(self) -> Json:
        data = super().to_json()
        data["type"]["params"]["branchingQuestion"]["question"] = format_text(self.question)
        data["type"]["params"]["branchingQuestion"]["alternatives"] = [alt.to_json() for alt in self.alternatives]
        return data


@dataclass(frozen=True)
class ALLiteral:
    symbol: str
    is_negated: bool

    def __invert__(self):
        return ALLiteral(self.symbol, not self.is_negated)

    def __str__(self) -> str:
        return f"¬{self.symbol}" if self.is_negated else self.symbol


type Rule = Literal["UPR", "PLR"]


@dataclass
class SimplifyChoice:
    rule: Rule
    literal: ALLiteral
    correct: bool

    def __str__(self) -> str:
        return f"{self.rule} mit {self.literal}"


@dataclass
class Formula:
    clauses: list[list[ALLiteral]]

    def symbols(self) -> list[str]:
        return sorted({lit.symbol for clause in self.clauses for lit in clause})

    def rules(self) -> Iterable[SimplifyChoice]:
        literals = {lit for clause in self.clauses for lit in clause}
        condition: dict[Rule, Callable[[ALLiteral], bool]] = {
            "UPR": lambda lit: [lit] in self.clauses,
            "PLR": lambda lit: lit in literals and ~lit not in literals,
        }
        for symbol in self.symbols():
            for rule in ("UPR", "PLR"):
                for is_negated in (False, True):
                    lit = ALLiteral(symbol, is_negated=is_negated)
                    yield SimplifyChoice(rule, lit, condition[rule](lit))

    def __str__(self) -> str:
        return "∧".join("(" + "∨".join(str(lit) for lit in clause) + ")" for clause in self.clauses)


@dataclass
class SimplifyHistory:
    rule: Rule
    literal: ALLiteral
    value: Literal[0, 1]
    formula: Formula

    def __str__(self) -> str:
        return f"{self.rule} mit λ = {self.literal} setzt 𝔄({self.literal.symbol}) = {self.value} und liefert\n{self.formula}"


def simplify_rules(formula: Formula, history: list[SimplifyHistory], choices: list[SimplifyChoice]) -> Presentation:
    steps = "\n".join(str(elem) for elem in history)
    formula_text = Text(0, 0, 100, 10, f"Aktuelle Formel: {formula}")
    history_text = Text(0, 10, 50, 90, f"Bisherige Simplify Schritte:\n{steps}")
    answers = [MultipleChoiceAnswer(str(choice), correct=choice.correct) for choice in formula.rules()]
    question = MultipleChoiceQuestion(50, 10, 50, 90, "Welche Vereinfachungsregeln lassen sich anwenden?", answers)
    return Presentation(f"Simplify {formula}", [formula_text, history_text, question])


if __name__ == "__main__":
    P, Q, R, S = [ALLiteral(symbol, is_negated=False) for symbol in "PQRS"]
    formula = Formula([[Q, P], [R, ~Q, ~P], [~Q, ~S, P], [~R]])
    rules = list(formula.rules())
    first = simplify_rules(formula, [], rules)
    # bundle_template(Path(__file__).parent / "templates" / "template.h5p")
    first.package_task(Path("test.h5p"))
