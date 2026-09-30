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


def test_add_interactive_rental_with_units(data_dir):
    answers = "\n".join(
        [
            "Test Duplex", "1 Fake St", "y", "2",
            "1A", "1,800", "Tenant A", "2026-01-01", "2026-12-31",  # unit 1
            "", "1,200", "", "", "",  # unit 2: default number, no tenant or lease
        ]
    ) + "\n"
    result = run("property", "add", input=answers)
    assert result.exit_code == 0, result.output
    assert "Added Test Duplex (rental, 2 units)" in result.output

    listing = run("property", "list").output
    assert "Test Duplex" in listing
    assert "$3,000.00" in listing  # total of both units

    shown = run("property", "show", "Test Duplex").output
    assert "1A" in shown
    assert "Tenant A" in shown
    assert "$1,200.00" in shown


def test_add_interactive_personal_skips_rental_questions(data_dir):
    result = run("property", "add", input="Home\n2 Fake Ave\nn\n")
    assert result.exit_code == 0, result.output
    assert "How many units" not in result.output
    assert "Added Home (personal)" in result.output


def test_add_with_flags(data_dir):
    result = run("property", "add", "Fourplex", "--rental", "--units", "4")
    assert result.exit_code == 0, result.output
    assert "4 units" in result.output


def test_units_flag_needs_rental(data_dir):
    result = run("property", "add", "Home", "--personal", "--units", "2")
    assert result.exit_code == 1
    assert "Only rental properties have units" in result.output


def test_add_rejects_bad_rent(data_dir):
    result = run("property", "add", input="Duplex\n\ny\n1\n1\nlots\n")
    assert result.exit_code == 1
    assert "Not a valid amount" in result.output


def test_edit_switch_to_personal_needs_confirmation(data_dir):
    run("property", "add", "Duplex", "--rental", "--units", "2")
    declined = run("property", "edit", "Duplex", "--personal", input="n\n")
    assert "2 unit(s)" in declined.output
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
