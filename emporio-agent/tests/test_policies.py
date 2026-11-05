import pytest

from emporio.policies import POLICIES_DIR, TOPICS, get_policy


@pytest.mark.parametrize("topic", TOPICS)
def test_every_topic_loads_and_is_not_empty(topic):
    result = get_policy(topic)
    assert result["topic"] == topic
    assert len(result["text"].strip()) > 200


def test_topics_match_the_files_on_disk():
    assert {path.stem for path in POLICIES_DIR.glob("*.md")} == set(TOPICS)


def test_topic_lookup_ignores_case_and_spaces():
    assert get_policy("  Returns ")["topic"] == "returns"


def test_unknown_topic_lists_the_valid_ones():
    result = get_policy("discounts")
    assert result["error"] == "unknown_topic"
    for topic in TOPICS:
        assert topic in result["message"]


# Sentences copied verbatim from data/raw/políticas_da_loja.pdf: the markdown must keep
# the manual's original wording.
@pytest.mark.parametrize(
    ("topic", "sentence"),
    [
        ("store_info", "Trabalhamos exclusivamente com instrumentos musicais — não comercializamos "
         "acessórios como cordas, palhetas, cabos, cases, pedais ou amplificadores."),
        ("store_info", "Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333"),
        ("hours", "| Segunda a Sexta-feira | 09:00 às 18:00 |"),
        ("hours", "| Sábado | 09:00 às 13:00 |"),
        ("payment", "| PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela |"),
        ("payment", "Parcelamento de 4x a 6x: sem juros, parcela mínima de R$ 80,00."),
        ("returns", "O cliente pode solicitar a devolução em até 7 (sete) dias corridos após o "
         "recebimento do produto, sem necessidade de justificativa."),
        ("returns", "O reembolso será realizado na mesma forma de pagamento original em até 10 "
         "dias úteis."),
        ("returns", "Produtos com defeito de fabricação podem ser trocados em até 30 (trinta) "
         "dias corridos após a compra."),
        ("shipping", "Para pedidos abaixo de R$ 500,00, taxa fixa de R$ 35,00."),
        ("shipping", "| SEDEX (Correios) | 2 a 5 dias úteis | Sim | Incluído |"),
        ("promotions", "O desconto de PIX (5%) não se aplica sobre preços já promocionais."),
        ("warranty", "todos os produtos possuem garantia legal de 90 (noventa) dias contra "
         "defeitos de fabricação, contados a partir da data de recebimento pelo cliente."),
        ("privacy", "Dados de clientes não são compartilhados com terceiros para fins de "
         "marketing."),
        ("scope_and_tone", "Reclamação:** ouvir o cliente com empatia, registrar a reclamação e "
         "encaminhar para o responsável. Prazo de retorno: 24 horas úteis."),
    ],
)
def test_original_wording_is_kept(topic, sentence):
    assert sentence in get_policy(topic)["text"]


@pytest.mark.parametrize(
    ("topic", "must_contain"),
    [
        ("payment", "R$ 50,00 up to 3x"),
        ("store_info", "(67) 3341-4444"),
        ("store_info", "contato@emporiodamusica.com.br"),
        ("scope_and_tone", "(67) 3341-4444"),
        ("scope_and_tone", "never name or invent one"),
        ("promotions", "Only promotions marked as active"),
    ],
)
def test_editorial_notes_are_present_and_marked(topic, must_contain):
    text = get_policy(topic)["text"]
    note = text.split("Editorial note (not part of the original manual):**", 1)
    assert len(note) == 2, f"{topic} has no marked editorial note"
    assert must_contain in note[1].split("\n\n", 1)[0]


def test_no_section_numbers_in_headings():
    # The agent must not cite "section 3" to customers, so headings carry no numbering.
    for topic in TOPICS:
        for line in get_policy(topic)["text"].splitlines():
            if line.startswith("#"):
                assert not line.lstrip("# ")[:1].isdigit(), f"{topic}: {line}"
