from typer.testing import CliRunner

from proptrack.cli import app

runner = CliRunner()


def run(*args, input=None):
    return runner.invoke(app, list(args), input=input)


def test_init_creates_database(data_dir):
    result = run("init")
    assert result.exit_code == 0, result.output
    assert (data_dir / "tracker.db").exists()
    assert (data_dir / "receipts").is_dir()


def test_add_interactive_rental(data_dir):
    answers = "Test Duplex\n1 Fake St\ny\n1,800\nTenant A\n2026-01-01\n2026-12-31\n"
    result = run("property", "add", input=answers)
    assert result.exit_code == 0, result.output
    assert "Added Test Duplex (rental)" in result.output

    listing = run("property", "list")
    assert "Test Duplex" in listing.output
    assert "$1,800.00" in listing.output


def test_add_interactive_personal_skips_rent_questions(data_dir):
    result = run("property", "add", input="Home\n2 Fake Ave\nn\n")
    assert result.exit_code == 0, result.output
    assert "Monthly rent" not in result.output
    assert "Added Home (personal)" in result.output


def test_add_with_flags(data_dir):
    result = run("property", "add", "Home", "--personal", "--address", "2 Fake Ave")
    assert result.exit_code == 0, result.output


def test_add_rejects_bad_rent(data_dir):
    result = run("property", "add", "Duplex", "--rental", "--rent", "lots")
    assert result.exit_code == 1
    assert "Not a valid amount" in result.output


def test_edit_switch_to_personal_needs_confirmation(data_dir):
    run("property", "add", "Duplex", "--rental", "--rent", "1000")
    declined = run("property", "edit", "Duplex", "--personal", input="n\n")
    assert "will be cleared" in declined.output
    assert "(rental)" in run("property", "show", "Duplex").output

    accepted = run("property", "edit", "Duplex", "--personal", "--yes")
    assert accepted.exit_code == 0, accepted.output
    assert "(personal)" in accepted.output


def test_show_lists_categories_for_type(data_dir):
    run("property", "add", "Duplex", "--rental")
    run("property", "add", "Home", "--personal")
    assert "Management fees" in run("property", "show", "Duplex").output
    home = run("property", "show", "Home").output
    assert "HOA fees" in home
    assert "Management fees" not in home


def test_unknown_property(data_dir):
    result = run("property", "show", "Nowhere")
    assert result.exit_code == 1
    assert "No property named" in result.output


def test_delete(data_dir):
    run("property", "add", "Mistake", "--personal")
    result = run("property", "delete", "Mistake", "--yes")
    assert result.exit_code == 0, result.output
    assert "No properties yet" in run("property", "list").output


def test_categories_command(data_dir):
    result = run("categories")
    assert result.exit_code == 0, result.output
    assert "Repairs" in result.output
    assert "depreciated" in result.output
