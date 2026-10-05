import crypto from "node:crypto";
import { test, expect } from "playwright/test";

const PROJECT_URL =
  "https://github.com/users/riley-berg/projects/5/views/1";

const username = process.env.PASI_GITHUB_USERNAME;
const password = process.env.PASI_GITHUB_PASSWORD;
const totpSecret = process.env.PASI_GITHUB_TOTP_SECRET;

if (!username || !password) {
  throw new Error(
    "PASI_GITHUB_USERNAME and PASI_GITHUB_PASSWORD must be configured as GitHub Actions secrets."
  );
}

function base32Decode(value) {
  const normalized = value.replace(/[\s-]/g, "").toUpperCase().replace(/=+$/g, "");
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let buffer = 0;
  const output = [];

  for (const char of normalized) {
    const index = alphabet.indexOf(char);
    if (index < 0) {
      throw new Error("PASI_GITHUB_TOTP_SECRET is not valid base32.");
    }
    buffer = (buffer << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      output.push((buffer >> bits) & 0xff);
    }
  }

  return Buffer.from(output);
}

function generateTotp(secret, now = Date.now()) {
  const key = base32Decode(secret);
  const counter = Math.floor(now / 1000 / 30);
  const data = Buffer.alloc(8);

  data.writeUInt32BE(Math.floor(counter / 0x100000000), 0);
  data.writeUInt32BE(counter >>> 0, 4);

  const digest = crypto.createHmac("sha1", key).update(data).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const code =
    ((digest[offset] & 0x7f) << 24) |
    (digest[offset + 1] << 16) |
    (digest[offset + 2] << 8) |
    digest[offset + 3];

  return String(code % 1_000_000).padStart(6, "0");
}

async function signIn(page) {
  await page.goto("https://github.com/login", {
    waitUntil: "domcontentloaded",
  });

  await page.getByLabel("Username or email address").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /^Sign in$/i }).click();

  const otp = page.locator(
    'input[autocomplete="one-time-code"], input[name="app_otp"], input[name="otp"]'
  ).first();

  if (await otp.isVisible().catch(() => false)) {
    if (!totpSecret) {
      throw new Error(
        "GitHub requested two-factor authentication, but PASI_GITHUB_TOTP_SECRET is not configured. " +
        "Use a dedicated test account with TOTP and store its setup secret as the repository secret."
      );
    }

    await otp.fill(generateTotp(totpSecret));

    const verify = page.getByRole("button", {
      name: /^(Verify|Continue)$/i,
    }).first();

    if (await verify.isVisible().catch(() => false)) {
      await verify.click();
    } else {
      await otp.press("Enter");
    }
  }

  await page.waitForTimeout(1500);

  if (page.url().includes("/login")) {
    const body = await page.locator("body").innerText().catch(() => "");
    throw new Error(
      "GitHub browser login did not complete. " +
      `Current URL: ${page.url()}\n${body.slice(0, 1200)}`
    );
  }
}

async function controlDiagnostics(popup, slot) {
  return popup.evaluate((root, requestedSlot) => {
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };

    const labels = [...root.querySelectorAll("*")].filter(
      (element) => element.textContent?.trim() === requestedSlot && visible(element)
    );

    const scopes = [];
    for (const label of labels) {
      if (label instanceof HTMLLabelElement && label.htmlFor) {
        const target = root.querySelector(`#${CSS.escape(label.htmlFor)}`);
        if (target) {
          scopes.push(target.parentElement ?? target);
        }
      }

      const fieldset = label.closest("fieldset");
      if (fieldset) scopes.push(fieldset);

      const group = label.closest('[role="group"]');
      if (group) scopes.push(group);

      let ancestor = label.parentElement;
      for (let depth = 0; ancestor && depth < 5; depth += 1) {
        scopes.push(ancestor);
        ancestor = ancestor.parentElement;
      }
    }

    const uniqueScopes = [...new Set(scopes)];

    for (const scope of uniqueScopes) {
      const controls = [...scope.querySelectorAll(
        'button, [role="combobox"], input, select'
      )].filter(visible);

      if (!controls.length || controls.length > 4) continue;

      const controlData = controls.map((control) => ({
        tag: control.tagName,
        text: control.textContent?.trim() ?? "",
        ariaLabel: control.getAttribute("aria-label") ?? "",
        ariaLabelledBy: control.getAttribute("aria-labelledby") ?? "",
        value: "value" in control ? String(control.value ?? "") : "",
        role: control.getAttribute("role") ?? "",
      }));

      const hasOtherSlot =
        requestedSlot === "Start date"
          ? controlData.some((item) => item.text === "Target date")
          : controlData.some((item) => item.text === "Start date");

      if (hasOtherSlot) continue;

      return {
        slot: requestedSlot,
        controlData,
      };
    }

    return {
      slot: requestedSlot,
      controlData: [],
      popupText: root.innerText,
    };
  }, slot);
}

test.describe("GitHub Project #5 Roadmap UI contract", () => {
  test("Start date and Target date are mapped to the matching fields", async ({
    page,
  }) => {
    test.setTimeout(90_000);

    await signIn(page);

    await page.goto(PROJECT_URL, {
      waitUntil: "domcontentloaded",
    });

    await expect(
      page.getByRole("tab", { name: "PASI Roadmap" })
    ).toHaveAttribute("aria-selected", "true", { timeout: 30_000 });

    const dateFieldsButton = page.getByRole("menuitem", {
      name: /^Select date fields$/i,
    });

    await expect(dateFieldsButton).toBeVisible({ timeout: 30_000 });
    await dateFieldsButton.click();

    const popup = page
      .locator('[role="menu"], [role="dialog"]')
      .filter({ hasText: "Start date" })
      .last();

    await expect(popup).toBeVisible({ timeout: 10_000 });

    const start = await controlDiagnostics(popup, "Start date");
    const target = await controlDiagnostics(popup, "Target date");

    expect(
      start.controlData.length,
      `Could not identify the Start date mapping control. Popup text:\n${start.popupText ?? "<unavailable>"}`
    ).toBeGreaterThan(0);

    expect(
      target.controlData.length,
      `Could not identify the Target date mapping control. Popup text:\n${target.popupText ?? "<unavailable>"}`
    ).toBeGreaterThan(0);

    const startText = JSON.stringify(start.controlData);
    const targetText = JSON.stringify(target.controlData);

    expect(
      startText,
      `Start date is not mapped to Start date. Observed: ${startText}`
    ).toMatch(/Start date/);

    expect(
      targetText,
      `Target date is not mapped to Target date. Observed: ${targetText}`
    ).toMatch(/Target date/);

    expect(
      startText,
      `Start date appears mapped to Target date: ${startText}`
    ).not.toContain("Target date");

    expect(
      targetText,
      `Target date appears mapped to Start date: ${targetText}`
    ).not.toContain("Start date");
  });
});
