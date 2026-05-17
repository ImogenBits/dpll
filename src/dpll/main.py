import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4
from zipfile import ZipFile

type Json = dict[str, Any]


@dataclass
class Literal:
    symbol: str
    positive: bool


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
        data = get_template(type(self).__name__)
        data["type"]["subContentId"] = str(self.subcontent_id)
        return data


@dataclass
class OuterElement(Element):
    title: str
    index: int | None = field(default=None, init=False)

    def to_json(self) -> Json:
        data = super().to_json()
        data["type"]["metadata"]["title"] = self.title
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
            format_text(self.top_text)
            + format_text("*" + "/".join(self.answers) + "*")
            + format_text(self.bottom_text)
        )
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


if __name__ == "__main__":
    last = MultipleChoiceQuestion(
        "This has many options",
        "Which of these are even?",
        [MultipleChoiceAnswer(str(i), i % 2 == 0) for i in range(12)],
    )
    yep = MultipleChoiceQuestion(
        "This has only a few options",
        "Are red pandas the best?",
        [
            MultipleChoiceAnswer("obviously!", correct=True),
            MultipleChoiceAnswer("nah", correct=False),
        ],
        next_question=last,
    )
    nope = Blanks(
        "Big Title!",
        "Task description",
        "Write in either thingy or nothing",
        "this also has a description",
        ["thingy", "nothing"],
        next_question=last,
    )
    first = BranchingQuestion(
        "Some Title",
        "Is the first question true?",
        [
            BranchingAlternative("yep", yep),
            BranchingAlternative("nope", nope),
        ],
    )
    #bundle_template(Path(__file__).parent / "templates" / "template.h5p")
    first.package_task(Path("test.h5p"))
