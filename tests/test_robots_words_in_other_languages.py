# -*- coding: utf-8 -*-
"""Account, cart and checkout routes are plumbing in every language.

A Vietnamese cosmetics shop's robots.txt closes `/checkout`, `/profile`,
`/thanh-toan`, `/tai-khoan` and `/homepage`. The English two were excused, and
the report's single worst thing was "robots.txt disallows 3 paths that look
like real content: /homepage, /tai-khoan, /thanh-toan" - the same checkout and
account pages in Vietnamese, and a second address for the front page.

Every host here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from robots_parser import benign_disallow, parse_robots  # noqa: E402


def _module(name, alias):
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module("crawl-access-audit", "ca_for_robots_words_tests")


@pytest.mark.parametrize("rule", [
    # Vietnamese, as slugs, with their accents, and percent-encoded
    "/tai-khoan", "/thanh-toan", "/gio-hang", "/dang-nhap", "/dang-ky", "/don-hang",
    "/tim-kiem", "/quan-tri", "/yeu-thich", "/tài-khoản", "/giỏ-hàng", "/đăng-nhập",
    "/t%C3%A0i-kho%E1%BA%A3n", "/vi/gio-hang", "/tai_khoan/",
    # Indonesian and Malay
    "/akun", "/masuk", "/daftar", "/keranjang", "/pembayaran", "/pesanan", "/cari",
    "/akaun", "/troli",
    # Thai, percent-encoded as it arrives in a robots.txt
    "/%E0%B8%95%E0%B8%B0%E0%B8%81%E0%B8%A3%E0%B9%89%E0%B8%B2",
    "/%E0%B8%8A%E0%B8%B3%E0%B8%A3%E0%B8%B0%E0%B9%80%E0%B8%87%E0%B8%B4%E0%B8%99",
    "/เข้าสู่ระบบ",
    # Spanish, Portuguese, French, German, Italian, Turkish
    "/mi-cuenta", "/carrito", "/finalizar-compra", "/pedidos", "/buscar", "/busqueda",
    "/minha-conta", "/carrinho", "/pagamento", "/meus-pedidos",
    "/mon-compte", "/panier", "/commande", "/connexion", "/paiement",
    "/konto", "/warenkorb", "/kasse", "/bestellung", "/suche", "/merkliste",
    "/carrello", "/ordini", "/accedi", "/lista-dei-desideri",
    "/hesabım", "/sepet", "/ödeme", "/sipariş", "/giriş", "/üye-ol", "/arama",
])
def test_the_same_route_in_another_language_is_plumbing(rule):
    assert benign_disallow(rule) is True, rule


@pytest.mark.parametrize("rule", ["/homepage", "/home", "/home/", "/home$", "/trang-chu",
                                  "/inicio", "/accueil", "/startseite", "/beranda"])
def test_a_second_address_for_the_front_page_is_not_content(rule):
    assert benign_disallow(rule) is True, rule


@pytest.mark.parametrize("rule", [
    # A word that continues into content keeps the rule reportable.
    "/daftar-harga", "/cuenta-corriente", "/home-decor", "/homepage-builder", "/home/garden",
    # Words whose first meaning is content are not in the vocabulary.
    "/recherche", "/ricerca", "/administration", "/anmeldung",
    # And an ordinary section in any language is still a section.
    "/san-pham", "/bo-suu-tap", "/koleksi", "/productos",
])
def test_content_in_another_language_is_still_reported(rule):
    assert benign_disallow(rule) is False, rule


def test_the_shops_own_file_raises_no_content_path_finding():
    robots = parse_robots(
        "User-agent: *\nDisallow: /homepage\nDisallow: /checkout\nDisallow: /thanh-toan\n"
        "Disallow: /profile\nDisallow: /tai-khoan\n")
    robots["status"] = 200
    result = SkillResult("crawl-access-audit")
    CA._check_robots_blocks(result, robots, "https://cosmetics-shop.test", [])
    assert not [f for f in result.findings if f["id_hint"] == "robots-blocks-content-paths"]
