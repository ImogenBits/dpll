import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Self
from uuid import UUID, uuid4
from zipfile import ZipFile

type Json = dict[str, Any]


def get_template(name: str) -> Json:
    if not name[0].islower():
        name = name[0].lower() + name[1:]
    path = Path(__file__).parent.joinpath("templates").joinpath(name).with_suffix(".json")
    return json.loads(path.read_text())


def format_text(text: str) -> str:
    return "".join(f"<p>{line.replace('\n', '</br>')}</p>" for line in text.split("\n\n"))


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


#####################
# DPPL recursion tree
#####################


@dataclass(frozen=True)
class ALLiteral:
    symbol: str
    is_negated: bool

    def __invert__(self):
        return ALLiteral(self.symbol, not self.is_negated)

    def __str__(self) -> str:
        return f"¬{self.symbol}" if self.is_negated else self.symbol


@dataclass(frozen=True)
class Formula:
    clauses: tuple[tuple[ALLiteral, ...], ...]

    def symbols(self) -> list[str]:
        return sorted({lit.symbol for clause in self.clauses for lit in clause})

    def rules(self) -> Iterable[RuleOption]:
        literals = {lit for clause in self.clauses for lit in clause}
        condition: dict[Rule, Callable[[ALLiteral], bool]] = {
            "UPR": lambda lit: (lit,) in self.clauses,
            "PLR": lambda lit: lit in literals and ~lit not in literals,
        }
        for symbol in self.symbols():
            for rule in ("UPR", "PLR"):
                for is_negated in (False, True):
                    lit = ALLiteral(symbol, is_negated=is_negated)
                    yield RuleOption(rule, lit, condition[rule](lit))

    def __str__(self) -> str:
        if not self.clauses:
            return "⊤"
        return "∧".join("(" + "∨".join(str(lit) for lit in clause) + ")" for clause in self.clauses)

    def ascii(self) -> str:
        return "&".join(
            "(" + "|".join(("!" if lit.is_negated else "") + lit.symbol for lit in clause) + ")"
            for clause in self.clauses
        )


type Rule = Literal["UPR", "PLR"]


@dataclass(frozen=True)
class RuleOption:
    rule: Rule
    literal: ALLiteral
    correct: bool

    def __str__(self) -> str:
        return f"{self.rule} mit {self.literal}"


@dataclass
class RuleApplication:
    rule: Rule
    literal: ALLiteral
    formula: Formula
    model: dict[str, int]

    def __str__(self) -> str:
        model = ", ".join(f"𝔄({sym}) = {val}" for sym, val in self.model.items())
        return f"{self.rule} mit λ = {self.literal} setzt {model} und liefert\n{self.formula}"

    @classmethod
    def from_rule_choice(cls, formula: Formula, rule: RuleOption) -> Self:
        new_formula = Formula(
            tuple(
                tuple(lit for lit in clause if ~rule.literal != lit)
                for clause in formula.clauses
                if rule.literal not in clause
            )
        )
        old_symbols = set(formula.symbols())
        model = dict.fromkeys((sym for sym in new_formula.symbols() if sym not in old_symbols), 0)
        model[rule.literal.symbol] = int(not rule.literal.is_negated)
        return cls(rule.rule, rule.literal, new_formula, model)


@dataclass
class State:
    formula: Formula
    history: list[RuleApplication]
    original_formula: Formula

    @classmethod
    def fresh(cls, formula: Formula) -> Self:
        return cls(formula, [], formula)


def with_history(title: str, question: MultipleChoiceQuestion, state: State, formula: Literal["curr", "orig"]) -> Presentation:
    question.x = 50
    question.y = 10
    question.width = 50
    question.height = 90
    steps = "\n\n".join(str(elem) for elem in state.history)
    if formula == "curr":
        formula_str = f"Aktuelle Formel: {state.formula}"
    else:
        formula_str = f"Ursprüngliche Formel: {state.original_formula}"
    formula_text = Text(0, 0, 100, 10, formula_str)
    history_text = Text(0, 10, 50, 90, f"Bisherige Simplify Schritte:\n\n{steps}")
    return Presentation(title, [formula_text, history_text, question])


def simplify_rules(state: State) -> Presentation:
    rules = list(state.formula.rules())
    if not rules:
        return dpll_next_step(state)
    correct = [choice for choice in rules if choice.correct]
    answers = [MultipleChoiceAnswer(str(choice), correct=choice.correct) for choice in rules]
    question = MultipleChoiceQuestion(0, 0, 0, 0, "Welche Vereinfachungsregeln lassen sich anwenden?", answers)
    rules_choice = with_history(f"Simplify {state.formula}", question, state, "curr")
    match correct:
        case []:
            rules_choice.next_question = dpll_next_step(state)
        case [choice]:
            rules_choice.next_question = simplify_apply(state, choice)
        case _:
            rules_choice.next_question = BranchingQuestion(
                "Choose " + ", ".join(str(choice) for choice in correct),
                "Welche der Optionen wollen Sie anwenden?",
                [BranchingAlternative(str(choice), simplify_apply(state, choice)) for choice in correct],
            )
    return rules_choice


def simplify_apply(state: State, rule: RuleOption) -> Presentation:
    application = RuleApplication.from_rule_choice(state.formula, rule)
    new_state = State(application.formula, [*state.history, application], state.original_formula)
    blanks = Blanks(
        0,
        0,
        100,
        100,
        "Trage die vereinfachte Formel ein. Nutze die computerlesbare Notation ohne "
        "Leerzeichen und beachte dabei die Klammerungsregeln in DPLL.",
        f"Wende {rule} an auf die Formel {state.formula}",
        f"Hinweis: in computerlesbarer Notation ist die Formel {state.formula.ascii()}",
        [str(application.formula), application.formula.ascii()],
    )
    return Presentation(f"Apply {rule} to {state.formula}", [blanks], next_question=simplify_rules(new_state))


def dpll_next_step(state: State) -> Presentation:
    formula = state.formula
    first = len(formula.clauses) == 0
    second = () in formula.clauses
    question = MultipleChoiceQuestion(
        0,
        0,
        100,
        100,
        f"Simplify gibt {formula} aus. Was ist das weitere Vorgehen von DPLL?",
        [
            MultipleChoiceAnswer("Die Formel ist gleich ⊤, wir geben eine Belegung zurück.", first),
            MultipleChoiceAnswer('Die Formel enthält  als Klausel, wir geben "unerfüllbar" zurück.', second),
            MultipleChoiceAnswer("Wir wählen ein Literal und wenden DPLL rekursiv an.", not first and not second),
        ],
    )
    if first:
        next_question = dpll_define_model(state)
    elif second:
        next_question = None
    else:
        next_question = dpll_choose_literal(state)
    return Presentation(f"DPLL Schritt {formula}", [question], next_question=next_question)


def dpll_choose_literal(state: State) -> BranchingQuestion:
    return BranchingQuestion(
        f"Choose Literal {state.formula}",
        "Mit welchen Literal wollen Sie fortfahren?",
        [
            BranchingAlternative(str(lit), dpll_apply_choice(state, lit))
            for symbol in state.formula.symbols()
            for lit in (ALLiteral(symbol, is_negated=False), ALLiteral(symbol, is_negated=True))
        ],
    )


def dpll_apply_choice(state: State, literal: ALLiteral) -> Presentation:
    new_formula = Formula((*state.formula.clauses, (literal,)))
    new_state = State(new_formula, state.history, state.original_formula)
    question = Blanks(
        0,
        0,
        100,
        100,
        "Trage die berechnete Formel ein. Nutze die computerlesbare Notation ohne "
        "Leerzeichen und beachte dabei die Klammerungsregeln in DPLL.",
        f"Die aktuelle Formel ist {state.formula}, das ausgewählte Literal ist {literal}."
        " Mit welcher Formel wird DPLL rekursiv aufgerufen?",
        f"Hinweis: in computerlesbarer Notation ist die Formel {state.formula.ascii()}.",
        [str(new_formula), new_formula.ascii()],
    )
    return Presentation(f"Apply Choice {literal}", [question], next_question=simplify_rules(new_state))


def dpll_define_model(state: State) -> Presentation:
    model = {k: v for application in state.history for k, v in application.model.items()}
    question = MultipleChoiceQuestion(
        0,
        0,
        0,
        0,
        "Welche Belegung gibt DPLL aus?\nWählen Sie die auf 1 gesetzten Literale aus.",
        [MultipleChoiceAnswer(symbol, bool(model[symbol])) for symbol in state.original_formula.symbols()],
    )
    return with_history("Define Model", question, state, "orig")


def notation_slide() -> Presentation:
    text = format_text(
        """In dieser Aufgabe werden wir die einzelnen Schritte des DPLL Algorithmus anwenden.
Um das etwas einfacher zu machen verwenden wir dafür eine vereinfachte computerlesbare Notation.

Dabei werden statt den logischen Junktoren ∧, ∨ und ¬ die ASCII Symbole &, | und ! verwendet.
Die formell notwendigen Klammern innerhalb jeder Klausel werden weggelassen, aber um jede Klausel
muss eine Klammer stehen. Insbesondere also auch um die leere Klausel und um welche die nur ein
Literal enthalten. Es sind auch keine Leerzeichen erlaubt.

Zum Beispiel wird die Formel "(P ∨ ¬Q) ∧ (R)" als "(P|!Q)&(R)" geschrieben und
"(()∧(P∨(¬Q∨R)))∧(Q∨¬S)" als "()&(P|!Q|R)&(Q|!S)".

Bei jeder Frage könnt ihr Teilpunkte erreichen. Falls ihr eine Frage falsch beantwortet könnt
ihr mit den weiteren Fragen weiter machen, ihr könnt aber nicht zurück und vorherige Aufgaben
korrigieren. Die in diesem System angezeigte "Punktzahl" ist nicht die Punkte die ihr insgesamt
zur Zulassung bekommt, sondern wird erst auf die für diese Aufgabe verteilte Punkte runter
gerechnet. Wenn ihr hier also z.B. 10 von 12 Fragen richtig beantwortet und die Aufgabe auf dem
Aufgabenblatt 3 Punkte gibt, bekommt ihr 2.5 Punkte.

"""
    )
    return Presentation("Notation", [Text(5, 5, 95, 95, text)])

def main():
    P, Q, R, S = [ALLiteral(symbol, is_negated=False) for symbol in "PQRS"]
    formula = Formula(((Q, P), (R, ~Q, ~P), (~Q, ~S, P), (~R,)))
    state = State.fresh(formula)
    notation = notation_slide()
    notation.next_question = simplify_rules(state)
    #bundle_template(Path(__file__).parent / "templates" / "template.h5p")
    notation.package_task(Path("test.h5p"))


if __name__ == "__main__":
    main()
