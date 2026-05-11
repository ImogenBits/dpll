import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4

type Json = dict[str, Any]


@dataclass
class Literal:
    symbol: str
    positive: bool


def get_template(name: str) -> Json:
    path = Path(__file__).parent.joinpath("templates").joinpath(name).with_suffix(".json")
    return json.loads(path.read_text())


def split_text(text: str) -> tuple[str, str]:
    split = len(text) // 2
    first: list[str] = []
    second: list[str] = []
    curr_len = 0
    for word in text.split(" "):
        curr_len += len(word) + 1
        if curr_len <= split:
            first.append(word)
        else:
            second.append(word)
    return " ".join(first), " ".join(second)


def format_text(text: str) -> str:
    return "".join(f"<p>{line}</p>" for line in text.splitlines())


@dataclass
class Question:
    title: str
    subcontent_id: UUID = field(default_factory=uuid4, init=False)
    index: int | None = field(default=None, init=False)

    template: ClassVar[str]

    def to_json(self) -> Json:
        data = get_template(self.template)
        data["type"]["metadata"]["title"] = self.title
        data["type"]["subContentId"] = str(self.subcontent_id)
        return data

    def build_task(self) -> str:
        questions: dict[UUID, Question] = {}
        found: list[Question] = [self]
        while found:
            curr = found.pop()
            questions[curr.subcontent_id] = curr
            if isinstance(curr, InPresentation) and curr.next_question is not None:
                found.append(curr.next_question)
            elif isinstance(curr, BranchingQuestion):
                found.extend(alternative.next_question for alternative in curr.alternatives)
        question_list = list(questions.values())
        for i, question in enumerate(question_list):
            question.index = i
        data = get_template("branchingScenario")
        data["branchingScenario"]["content"] = [question.to_json() for question in question_list]
        return json.dumps(data)


@dataclass
class InPresentation(Question, ABC):
    next_question: Question | None = field(default=None, kw_only=True)

    template: ClassVar = "presentation"

    def to_json(self) -> Json:
        data = super().to_json()
        data["nextContentId"] = self.next_question.index if self.next_question is not None else -1
        inner = self.inner_json()
        width = 100 // len(inner)
        data["type"]["params"]["presentation"]["slides"][0]["elements"] = [
            {
                "x": x,
                "y": 0,
                "width": width,
                "height": 100,
                "action": obj,
                "alwaysDisplayComments": False,
                "backgroundOpacity": 0,
                "displayAsButton": False,
                "buttonSize": "big",
                "goToSlideType": "specified",
                "invisible": False,
                "solution": "",
            }
            for (obj, x) in zip(inner, range(0, 100, width), strict=True)
        ]
        return data

    @abstractmethod
    def inner_json(self) -> list[Json]: ...


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
class MultipleChoiceQuestion(InPresentation):
    question: str
    answers: list[MultipleChoiceAnswer]

    def inner_json(self) -> list[Json]:
        if len(self.answers) > 8:
            first, second = split_text(self.question)
            data = [(first, self.answers[: len(self.answers) // 2]), (second, self.answers[len(self.answers) // 2 :])]
        else:
            data = [(self.question, self.answers)]
        questions: list[Json] = []
        for question, answers in data:
            multi_choice = get_template("multipleChoiceQuestion")
            multi_choice["params"]["question"] = format_text(question)
            multi_choice["params"]["answers"] = [answer.to_json() for answer in answers]
            questions.append(multi_choice)
        return questions


@dataclass
class Blanks(InPresentation):
    description: str
    top_text: str
    bottom_text: str
    answers: list[str]

    def inner_json(self) -> list[Json]:
        data = get_template("blanks")
        data["params"]["text"] = format_text(self.description)
        data["params"]["questions"][0] = (
            format_text(self.top_text)
            + format_text("*" + "/".join(self.answers) + "*")
            + format_text(self.bottom_text)
        )
        return [data]


@dataclass
class BranchingAlternative:
    text: str
    next_question: Question

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
class BranchingQuestion(Question):
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
    Path("content.json").write_text(first.build_task())
