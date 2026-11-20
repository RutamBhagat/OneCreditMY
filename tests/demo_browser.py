"""Smoke-test an API instance that has ingested the supplied feed."""

import sys

from playwright.sync_api import expect, sync_playwright

SOURCE = "SYN-001550"


def check_demo(browser, base_url, *, javascript):
    context = browser.new_context(base_url=base_url, java_script_enabled=javascript)
    try:
        page = context.new_page()
        source_url = f"/demo/{SOURCE}"
        assert page.goto("/").status == 200
        expect(page).to_have_url(base_url + source_url)
        details = page.locator('section[aria-labelledby="company-name"]')
        name = page.get_by_role("heading", level=1)
        expect(name).to_have_text("Sinar Teguh Bakery Sdn. Bhd.")
        expect(details).to_contain_text("Food & Beverage · Selangor")
        expect(details).to_contain_text("Operating cafes and coffee shops in the Klang Valley")
        recommendations = page.get_by_role("region", name="Similar companies")
        cards = recommendations.get_by_role("link")
        expect(cards).to_have_count(4)
        expect(recommendations.get_by_role("list")).to_have_css("display", "grid")
        source_links = [card.get_attribute("href") for card in cards.all()]
        source_json = context.request.get(f"/companies/{SOURCE}/similar").json()
        assert source_links == [f"/demo/{card['registration_number']}" for card in source_json]

        first_card = cards.first
        destination = first_card.get_attribute("href")
        destination_name = source_json[0]["name"]
        with page.expect_response(base_url + destination) as navigation:
            first_card.click()
        request = navigation.value.request
        assert navigation.value.ok
        if javascript:
            assert request.headers.get("hx-boosted") == "true"
        else:
            assert request.is_navigation_request()
        expect(page).to_have_url(base_url + destination)
        expect(name).to_have_text(destination_name)
        expect(details).not_to_contain_text(SOURCE)
        expect(cards).to_have_count(4)
        destination_links = [card.get_attribute("href") for card in cards.all()]
        assert destination_links != source_links
        expect(page).to_have_title(f"{destination_name} · OneCredit demo")

        page.go_back()
        expect(page).to_have_url(base_url + source_url)
        expect(name).to_have_text("Sinar Teguh Bakery Sdn. Bhd.")
        assert [card.get_attribute("href") for card in cards.all()] == source_links
        assert page.goto(destination).status == 200
        expect(name).to_have_text(destination_name)
        assert [card.get_attribute("href") for card in cards.all()] == destination_links
        assert page.goto("/demo/UNKNOWN-REGISTRATION").status == 404
        expect(name).to_have_text("Company not found")
    finally:
        context.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for javascript in (True, False):
            check_demo(browser, sys.argv[1].rstrip("/"), javascript=javascript)
            print(f"Browser smoke passed with JavaScript {'enabled' if javascript else 'disabled'}")
