import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cached_property
from itertools import product, starmap
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
    return "".join(f"<p>{line.replace('\n', '<br />')}</p>" for line in text.split("\n\n"))


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
    type: Literal["single", "multi"] = "multi"

    def to_json(self) -> Json:
        data = super().to_json()
        data["params"]["question"] = format_text(self.question)
        data["params"]["answers"] = [answer.to_json() for answer in self.answers]
        data["params"]["behaviour"]["type"] = self.type
        return data


@dataclass
class Blanks(PresentationElement):
    description: str
    text: str
    answers: list[list[str]]

    def to_json(self) -> Json:
        data = get_template("blanks")
        data["params"]["text"] = format_text(self.description)
        data["params"]["questions"][0] = format_text(
            self.text.format(*(f"*{'/' if options == [''] else '/'.join(options)}*" for options in self.answers))
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
class Table(PresentationElement):
    title: str
    text: str

    def to_json(self) -> dict[str, Any]:
        data = super().to_json()
        data["metadata"]["title"] = self.title
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
        return f"\\({self.latex()}\\)"

    def latex(self) -> str:
        return f"\\neg {self.symbol}" if self.is_negated else self.symbol


@dataclass(unsafe_hash=True)
class Formula:
    clauses: tuple[tuple[ALLiteral, ...], ...]
    short: str | None = field(default=None, hash=False, compare=False)

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
        return f"\\({self.latex()}\\)"

    def unicode(self) -> str:
        if not self.clauses:
            return "⊤"
        return "∧<wbr />".join("(" + "∨".join(str(lit) for lit in clause) + ")" for clause in self.clauses)

    def ascii(self) -> str:
        if not self.clauses:
            return "1"
        return "&".join(
            "(" + "|".join(("!" if lit.is_negated else "") + lit.symbol for lit in clause) + ")"
            for clause in self.clauses
        )

    def latex(self, *, force_long: bool = False) -> str:
        if self.short is not None and not force_long:
            return self.short
        if not self.clauses:
            return "\\top"
        return "\\land ".join(
            "(" + "\\lor ".join(("\\neg " if lit.is_negated else "") + lit.symbol for lit in clause) + ")"
            for clause in self.clauses
        )

    def valid_spellings(self) -> list[str]:
        if not self.clauses:
            return ["", "1", "⊤"]
        spellings: list[str] = []
        for func, bot in ((str, "⊥"), (Formula.ascii, "0"), (Formula.latex, "\\bot")):
            template = func(self).replace("()", "{}").replace("<wbr />", "")
            count = template.count("{}")
            spellings.extend(starmap(template.format, product(("()", bot), repeat=count)))
        return spellings

    def add_unit(self, literal: ALLiteral) -> Self:
        return type(self)(
            (*self.clauses, (literal,)), f"{self.short} \\land ({literal.latex()})" if self.short else None
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
        model = ", ".join(f"\\mathfrak A({sym}) = {val}" for sym, val in self.model.items())
        return (
            f"{self.rule} mit \\(\\lambda = {self.literal.latex()}\\) setzt \\({model}\\) und liefert\n{self.formula}"
        )

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
class CurrentSimplify:
    steps: list[RuleApplication]

    def __str__(self) -> str:
        steps = "\n\n".join(str(step) for step in self.steps)
        return f"Aktueller Simplify Aufruf:\n\n{steps}"

    def summarize(self, orig: Formula, chosen_literal: ALLiteral) -> SimplifyAndRecurse:
        simple_formula = self.steps[-1].formula if self.steps else orig
        model = {k: v for step in self.steps for k, v in step.model.items()}
        return SimplifyAndRecurse(orig, simple_formula, model, chosen_literal)


@dataclass
class SimplifyAndRecurse:
    orig_formula: Formula
    simple_formula: Formula
    model: dict[str, int]
    chosen_literal: ALLiteral

    def __str__(self) -> str:
        model = ", ".join(f"\\mathfrak A({sym}) = {val}" for sym, val in self.model.items())
        return (
            f"<p>Simplify auf {self.orig_formula} hat {self.simple_formula} und \\({model}\\) zurückgegeben.</br>"
            f"Rekursiver Aufruf von DPLL mit {self.simple_formula.add_unit(self.chosen_literal)}.</p>"
        )


FORMULA_NAMES = ["\\varphi", "\\psi", "\\theta", "\\chi", "\\eta"]


@dataclass
class State:
    formula: Formula
    history: list[SimplifyAndRecurse]
    current: CurrentSimplify
    original_formula: Formula

    @classmethod
    def fresh(cls, formula: Formula) -> Self:
        return cls(formula, [], CurrentSimplify([]), formula)

    def named_formulas(self) -> dict[Formula, str]:
        formulas = [
            self.original_formula,
            *(step.simple_formula for step in self.history),
            *(step.formula for step in self.current.steps),
        ]
        names: dict[Formula, str] = {formula: formula.short for formula in formulas if formula.short is not None}
        greek_letters = iter(FORMULA_NAMES)
        for formula in formulas:
            if formula.short is not None or len(formula.ascii()) < 32:
                continue
            if (
                formula.clauses
                and len(formula.clauses[-1]) == 1
                and (without_unit := Formula((*formula.clauses[:-1],))) in names
            ):
                formula.short = f"{names[without_unit]} \\land ({formula.clauses[-1][0].latex()})"
            else:
                formula.short = next(greek_letters)
            names[formula] = formula.short
        return names

    def __str__(self) -> str:
        return "\n\n".join(str(elem) for elem in self.history) + str(self.current)

    def dpll_choice(self, chosen_literal: ALLiteral) -> State:
        if self.history:
            step = self.history[-1]
            original_formula = step.simple_formula.add_unit(step.chosen_literal)
        else:
            original_formula = self.original_formula
        return State(
            formula=self.formula.add_unit(chosen_literal),
            history=[*self.history, self.current.summarize(original_formula, chosen_literal)],
            current=CurrentSimplify([]),
            original_formula=self.original_formula,
        )

    def simplify_step(self, step: RuleApplication) -> State:
        return State(
            formula=step.formula,
            history=self.history,
            current=CurrentSimplify([*self.current.steps, step]),
            original_formula=self.original_formula,
        )

    def model(self) -> dict[str, int]:
        return {k: v for step in self.history for k, v in step.model.items()} | {
            k: v for step in self.current.steps for k, v in step.model.items()
        }


def with_history(
    title: str, question: MultipleChoiceQuestion, state: State, formula: Literal["curr", "orig"]
) -> Presentation:
    names = state.named_formulas()
    header_height = 10 + 4 * (1 + len(names))
    question.x = 60
    question.y = header_height
    question.width = 38
    question.height = 90
    if formula == "curr":
        formula_str = f"Aktuelle Formel: {state.formula}"
    else:
        formula_str = f"Ursprüngliche Formel: {state.original_formula}"
    if names:
        formula_str += "\nBenannte Formeln:\n" + "\n".join(
            f"\\({name} = {formula.latex(force_long=True)}\\)" for formula, name in names.items()
        )
    formula_text = Text(0, 0, 100, header_height, formula_str)
    history_text = Table(2, header_height, 58, 90, "History", str(state))
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


SIMPLIFY_SPECIFY_MODEL = False


def simplify_apply(state: State, rule: RuleOption) -> Presentation:
    application = RuleApplication.from_rule_choice(state.formula, rule)
    new_state = state.simplify_step(application)
    if SIMPLIFY_SPECIFY_MODEL:
        symbols = state.original_formula.symbols()
        model_text = (
            "Welche Belegung wird in diesem Schritt berechnet? "
            "Trage die gesetzten Werte ein und lasse die übrigen Felder frei.\n"
            + "    ".join(f"{symbol}: {{}}" for symbol in symbols)
        )
        model_answers = [
            [str(application.model[symbol])]
            if symbol == rule.literal.symbol
            else ["0", "1"]
            if symbol in application.model
            else [""]
            for symbol in symbols
        ]
    else:
        model_text = ""
        model_answers = []
    blanks = Blanks(
        0,
        0,
        100,
        100,
        "Trage die vereinfachte Formel ein. Nutze die computerlesbare Notation ohne "
        "Leerzeichen und beachte dabei die Klammerungsregeln in DPLL.",
        f"Wende {rule} an auf die Formel \\({state.formula.latex(force_long=True)}\\)\n\n"
        "{}\n\n"
        f"Hinweis: in computerlesbarer Notation ist die Formel {state.formula.ascii()}\n\n" + model_text,
        [new_state.formula.valid_spellings(), *model_answers],
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
        f"Simplify gibt \\({formula.latex(force_long=True)}\\) aus. Was ist das weitere Vorgehen von DPLL?",
        [
            MultipleChoiceAnswer("Die Formel ist gleich \\(\\top\\), wir geben eine Belegung zurück.", first),
            MultipleChoiceAnswer('Die Formel enthält \\(\\bot\\) als Klausel, wir geben "unerfüllbar" zurück.', second),
            MultipleChoiceAnswer("Wir wählen ein Literal und wenden DPLL rekursiv an.", not first and not second),
        ],
    )
    next_question = None if first or second else dpll_choose_literal(state)
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
    new_state = state.dpll_choice(literal)
    question = Blanks(
        0,
        0,
        100,
        100,
        "Trage die berechnete Formel ein. Nutze die computerlesbare Notation ohne "
        "Leerzeichen und beachte dabei die Klammerungsregeln in DPLL.",
        f"Die aktuelle Formel ist \\({state.formula.latex(force_long=True)}\\), das ausgewählte Literal ist {literal}."
        " Mit welcher Formel wird DPLL rekursiv aufgerufen?\n\n{}\n\n"
        f"Hinweis: in computerlesbarer Notation ist die Formel {state.formula.ascii()}.",
        [new_state.formula.valid_spellings()],
    )
    return Presentation(f"Apply Choice {literal}", [question], next_question=simplify_rules(new_state))


def dpll_define_model(state: State) -> Presentation:
    model = state.model()
    question = MultipleChoiceQuestion(
        0,
        0,
        0,
        0,
        "Welche Belegung gibt DPLL aus?\nWählen Sie die auf 1 gesetzten Literale aus.",
        [MultipleChoiceAnswer(symbol, bool(model[symbol])) for symbol in state.original_formula.symbols()],
    )
    return with_history("Define Model", question, state, "orig")


def notation_slide(*, is_graded: bool) -> Presentation:
    text = """In dieser Aufgabe werden wir die einzelnen Schritte des DPLL Algorithmus anwenden.
Um das etwas einfacher zu machen verwenden wir dafür eine vereinfachte computerlesbare Notation.

Dabei werden statt den logischen Junktoren \\(\\land, \\lor\\) und \\(\\neg\\) die ASCII Symbole &, | und ! verwendet.
Die formell notwendigen Klammern innerhalb jeder Klausel werden weggelassen, aber um jede Klausel
muss eine Klammer stehen. Insbesondere also auch um die leere Klausel und um welche die nur ein
Literal enthalten. Es sind auch keine Leerzeichen erlaubt. Für die leere Konjunktion kann man auch
\\(\\top\\) bzw. 1 schreiben, für die leere Klausel auch \\(\\bot\\) bzw. 0.

Zum Beispiel wird die Formel "\\((P \\lor \\neg Q) \\land (R)\\)" als "(P|!Q)&(R)" geschrieben und
"\\((() \\land (P \\lor (\\neg Q \\lor R))) \\land (Q \\lor \\neg S)\\)" als "()&(P|!Q|R)&(Q|!S)".
"""
    if is_graded:
        text += """\n\nBei jeder Frage könnt ihr Teilpunkte erreichen. Falls ihr eine Frage falsch beantwortet könnt
ihr mit den weiteren Fragen weiter machen, ihr könnt aber nicht zurück und vorherige Aufgaben
korrigieren. Die in diesem System angezeigte "Punktzahl" ist nicht die Punkte die ihr insgesamt
zur Zulassung bekommt, sondern wird erst auf die für diese Aufgabe verteilte Punkte runter
gerechnet. Wenn ihr hier also z.B. 10 von 12 Fragen richtig beantwortet und die Aufgabe auf dem
Aufgabenblatt 3 Punkte gibt, bekommt ihr 2.5 Punkte.

"""
    else:
        text += """\n\nBei dieser Aufgabe erhaltet ihr keine Punkte. Die hier angezeigte Anzahl von richtigen und falschen
Antworten ist also nicht zulassungsrelevant."""
    
    return Presentation("Notation", [Text(5, 5, 95, 95, format_text(text))])


#######################################################
## DPLL Choice
#######################################################


@dataclass
class RecursionState:
    history: list[RecursionLevel]
    index: int

    @property
    def formula(self) -> Formula:
        return self.history[self.index].after_simplify

    @property
    def original_formula(self) -> Formula:
        return self.history[0].formula

    @cached_property
    def heuristic(self) -> str:
        return (
            "\\("
            + ", ".join(
                lit.latex()
                for symbol in self.original_formula.symbols()
                for lit in (ALLiteral(symbol, is_negated=False), ALLiteral(symbol, is_negated=True))
            )
            + "\\)"
        )

    @property
    def named_formulas(self) -> dict[str, str]:
        return {
            formula.short: formula.latex(force_long=True)
            for elem in self.history
            for formula in (elem.formula, elem.after_simplify)
            if formula.short is not None and formula.short.find("(") < 0
        }

    def recurse(self, level: RecursionLevel) -> None:
        self.history.append(level)
        self.index = len(self.history) - 1

    def backtrack(self, level: int) -> None:
        self.index = level


@dataclass
class RecursionLevel:
    formula: Formula
    after_simplify: Formula
    model: dict[str, int]
    ret: Literal["unsat"] | None = None

    def __str__(self) -> str:
        val = f"DPLL aufgerufen mit {self.formula}.\n"
        if self.formula == self.after_simplify:
            val += "Simplify kann die Formel nicht vereinfachen."
        else:
            model = ", ".join(f"𝔄({sym}) = {val}" for sym, val in self.model.items())
            val += f"Simplify liefert {self.after_simplify} und {model}."
        if self.ret is not None:
            val += '\nAufruf hat "unerfüllbar" zurückgegeben.'
        return val


def with_recursion_history(
    state: RecursionState, question_text: str, answers: list[MultipleChoiceAnswer]
) -> Presentation:
    header_height = 18 + 4 * (1 + len(state.named_formulas))
    header = Text(
        0,
        2,
        100,
        header_height,
        f"Die aktuelle Formel nach anwendung von Simplify ist: {state.formula}\n"
        f"DPLL wählt in jedem Schritt das erste mögliche Literal in dieser Reihenfolge: {state.heuristic}"
        + ("\nBenannte Formeln:" if state.named_formulas else "")
        + "".join(f"\n\\({name} = {formula}\\)" for name, formula in state.named_formulas.items()),
    )
    history = Table(
        2,
        header_height + 2,
        46,
        96 - header_height,
        "Bisherige DPLL Aufrufe",
        "Bisherige DPLL Aufrufe:\n\n" + "\n\n".join(f"{i + 1}: {elem}" for i, elem in enumerate(state.history)),
    )
    question = MultipleChoiceQuestion(
        50,
        header_height + 2,
        48,
        96 - header_height,
        question_text,
        answers,
        type="single",
    )
    return Presentation(f"Step {state.formula}", [header, history, question])


def recursion_step(state: RecursionState, chosen_lit: ALLiteral | None) -> Presentation:
    return with_recursion_history(
        state,
        f"Was ist der nächste Schritt von DPLL Aufruf {state.index + 1}?",
        [
            MultipleChoiceAnswer("Eine Belegung zurückgeben.", correct=False),
            MultipleChoiceAnswer('Return "unerfüllbar" aus dem aktuellen Aufruf.', correct=chosen_lit is None),
            *(
                MultipleChoiceAnswer(f"DPLL rekursiv mit {lit} aufrufen.", lit == chosen_lit)
                for symbol in state.original_formula.symbols()
                for lit in (ALLiteral(symbol, is_negated=False), ALLiteral(symbol, is_negated=True))
            ),
        ],
    )


def backtrack_step(state: RecursionState, correct: int | None) -> Presentation:
    return with_recursion_history(
        state,
        "Wird der Algorithmus beendet? Wenn nicht, zu welchem Rekursionsschritt wird zurück gesprungen?",
        [
            MultipleChoiceAnswer('Der Algorithmus terminiert mit "unerfüllbar"', correct=correct is None),
            *(MultipleChoiceAnswer(f"Schritt {i + 1}", correct=i == correct) for i, elem in enumerate(state.history)),
        ],
    )


def recursion_define_model(state: RecursionState, model: dict[str, int]) -> Presentation:
    header = Text(2, 2, 98, 12, f"Die ursprüngliche Formel ist: {state.original_formula}")
    history = Text(
        2,
        12,
        48,
        86,
        "Bisherige DPLL Aufrufe:\n\n" + "\n\n".join(str(elem) for elem in state.history),
    )
    question = MultipleChoiceQuestion(
        0,
        0,
        0,
        0,
        "Welche Belegung gibt DPLL aus?\nWählen Sie die auf 1 gesetzten Literale aus.",
        [MultipleChoiceAnswer(symbol, bool(model[symbol])) for symbol in state.original_formula.symbols()],
    )
    return Presentation(f"Step {state.formula}", [header, history, question])


def aufgabe_1() -> OuterElement:
    P, Q, R, S = [ALLiteral(symbol, is_negated=False) for symbol in "PQRS"]
    formula = Formula(((Q, P), (R, ~Q, ~P), (~Q, ~S, P), (~R,)))
    state = State.fresh(formula)
    return simplify_rules(state)


def aufgabe_2() -> OuterElement:
    P, Q, R, S = [ALLiteral(symbol, is_negated=False) for symbol in "PQRS"]
    phi = Formula(
        (
            (P, ~Q),
            (~P, Q),
            (~P, ~Q),
            (S, ~Q, R),
            (~S, ~R, P),
            (~S, R),
            (~R, S),
        ),
        "\\varphi",
    )
    state = RecursionState([RecursionLevel(phi, phi, {})], 0)
    first = curr = recursion_step(state, P)

    psi = Formula(
        (
            (),
            (~S, R),
            (~R, S),
        ),
        "\\psi",
    )
    level = RecursionLevel(phi.add_unit(P), psi, {"P": 1, "Q": 1})
    state.recurse(level)
    curr.next_question = curr = recursion_step(state, None)

    curr.next_question = curr = backtrack_step(state, 0)
    state.history[1].ret = "unsat"
    state.backtrack(0)

    curr.next_question = curr = recursion_step(state, ~P)

    psi_prime = Formula(
        (
            (S, R),
            (~S, ~R),
            (~S, R),
            (~R, S),
        ),
        "\\psi'",
    )
    level = RecursionLevel(phi.add_unit(~P), psi_prime, {"P": 0, "Q": 0})
    state.recurse(level)
    curr.next_question = curr = recursion_step(state, R)

    theta = Formula(((),))
    level = RecursionLevel(psi_prime.add_unit(R), theta, {"R": 1, "S": 0})
    state.recurse(level)
    curr.next_question = curr = recursion_step(state, None)

    curr.next_question = curr = backtrack_step(state, 2)
    state.history[3].ret = "unsat"
    state.backtrack(2)

    curr.next_question = curr = recursion_step(state, ~R)

    theta_prime = theta
    level = RecursionLevel(psi_prime.add_unit(~R), theta_prime, {"R": 0, "S": 1})
    state.recurse(level)
    curr.next_question = curr = recursion_step(state, None)

    curr.next_question = curr = backtrack_step(state, 2)
    state.history[4].ret = "unsat"
    state.backtrack(2)

    curr.next_question = curr = recursion_step(state, None)

    curr.next_question = curr = backtrack_step(state, 0)
    state.history[2].ret = "unsat"
    state.backtrack(0)

    curr.next_question = curr = recursion_step(state, None)

    curr.next_question = curr = backtrack_step(state, None)
    state.history[0].ret = "unsat"

    return first


def bonus_1() -> OuterElement:
    P, Q, R, S = [ALLiteral(symbol, is_negated=False) for symbol in "PQRS"]
    formula = Formula(((~P, Q, R), (~P, ~R, ~Q), (P, Q), (~Q, S, R), (~S, P, ~Q)))
    state = State.fresh(formula)
    return simplify_rules(state)


if __name__ == "__main__":
    notation = notation_slide(is_graded=False)
    notation.next_question = bonus_1()
    # bundle_template(Path(__file__).parent / "templates" / "template.h5p")
    notation.package_task(Path("test.h5p"))
