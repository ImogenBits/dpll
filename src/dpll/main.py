import json
from dataclasses import dataclass
from itertools import starmap
from pathlib import Path
from typing import Any
from uuid import uuid4

type Json = dict[str, Any]


@dataclass
class Literal:
    symbol: str
    positive: bool


def get_template(name: str) -> Json:
    return json.loads(Path(__file__).parent.joinpath("templates").joinpath(name).with_suffix(".json").read_text())


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


def multiple_choice_answer(text: str, correct: bool) -> Json:
    answer = get_template("multiChoiceAnswer")
    answer["correct"] = correct
    answer["text"] = f"<div>{text}<\\/div>"
    return answer


def multiple_choice_question(title: str, question: str, answers: list[tuple[str, bool]]) -> Json:
    presentation = get_template("presentation")
    presentation["type"]["metadata"]["title"] = title
    presentation["type"]["subContentId"] = str(uuid4())
    if len(answers) > 8:
        first, second = split_text(question)
        data = [(first, answers[:len(answers) // 2]), (second, answers[len(answers) // 2:])]
        width = 50
    else:
        data = [(question, answers)]
        width = 100
    for question, answers in data:
        multi_choice = get_template("multipleChoice")
        multi_choice["width"] = width
        multi_choice["question"] = f"<p>{question}<\\/p>"
        multi_choice["action"]["params"]["answers"] = list(starmap(multiple_choice_answer, answers))
        presentation["type"]["params"]["presentation"]["slides"][0]["elements"].append(multi_choice)
    return presentation
