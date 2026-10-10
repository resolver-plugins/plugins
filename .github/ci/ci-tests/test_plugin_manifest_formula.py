"""Regression coverage for optional plugin dependency solver formulas."""

from common_imports import *

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('formula_dependencies, expected', [
    ('bind920', ['python311']), ('', ['bind920', 'python311']),
])
def test_framework_omits_only_formula_dependencies_from_exact_revision_pins(formula_dependencies, expected):
    framework = (REPOSITORY_ROOT / 'Mk/plugins.mk').read_text(encoding='utf-8')
    # Execute the real recipe bodies with make variables supplied through the shell.
    # The surrounding defined() conditions are selected explicitly for this fixture.
    recipe = '\n'.join(framework.split(f'.if defined({name})\n', 1)[1].split('.endif', 1)[0]
                       for name in ('PLUGIN_DEPENDS', 'PLUGIN_DEPEND_FORMULA'))
    recipe = re.sub(r'^\t@', '', recipe, flags=re.MULTILINE).replace('$$', '$')
    recipe = 'pkg() { printf \'  %s: { version: "1.0", origin: "fixture/%s" }\\n\' "$3" "$3"; }\n' + recipe
    env = dict(os.environ, PKG='pkg', PLUGIN_DEPENDS='bind920 python311',
               PLUGIN_DEPEND_FORMULA_DEPENDS=formula_dependencies,
               PLUGIN_DEPEND_FORMULA='bind920 >= 9.20.26')
    result = subprocess.run(['sh', '-eu'], input=recipe, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        'deps: {', *[f'  {name}: {{ version: "1.0", origin: "fixture/{name}" }}' for name in expected],
        '}', 'dep_formula: "bind920 >= 9.20.26"',
    ]


def test_bind_links_its_dependency_formula_to_the_bind920_manifest_entry():
    makefile = (REPOSITORY_ROOT / 'dns/bind/Makefile').read_text(encoding='utf-8')
    assert re.search(r'^PLUGIN_DEPEND_FORMULA_DEPENDS=\s*bind920$', makefile, re.MULTILINE)
