from app.services import latex_check


def test_latex_check_accepts_balanced_math() -> None:
    assert latex_check("$x^2 + 1$")["errors"] == []


def test_latex_check_reports_unpaired_dollar() -> None:
    assert latex_check("$x^2 + 1")["errors"]
