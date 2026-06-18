import { test, expect } from "@playwright/test"
import { randomEmail, randomPassword } from "./utils/random"
import { createUser } from "./utils/privateApi"
import { logInUser } from "./utils/user"

test.describe("Knowledge bases", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  let email: string
  const password = randomPassword()

  test.beforeAll(async () => {
    email = randomEmail()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
  })

  test("Knowledge bases page is accessible and shows empty state", async ({ page }) => {
    await page.goto("/knowledge-bases")
    await expect(
      page.getByRole("heading", { name: "Knowledge Bases" }),
    ).toBeVisible()
    await expect(
      page.getByText("Create and manage your knowledge bases"),
    ).toBeVisible()
    await expect(
      page.getByText("You don't have any knowledge bases yet"),
    ).toBeVisible()
  })
})
