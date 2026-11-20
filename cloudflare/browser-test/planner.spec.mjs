import { readFile } from 'node:fs/promises';
import { test, expect } from '@playwright/test';

const workRowButton = page => page.getByRole('button', { name: '오늘의 중식 행 열 편집' });
const workColumns = page => page.locator('#notebook-home [data-row="work"] > .preview-column');

async function expectWorkWidths(page, expected) {
  await expect.poll(() => workColumns(page).evaluateAll(columns => columns.map(column => Number(column.dataset.ratio)))).toEqual(expected);
}

async function editWorkWidths(page) {
  await workRowButton(page).click();
  await page.getByRole('button', { name: '너비 같게' }).click();
  await expectWorkWidths(page, [50, 50]);
}

async function openPreviewFromQuestionnaire(page) {
  await page.goto('/setup');
  await page.locator('#schoolDeferred').check();
  await page.locator('#schoolLevel').selectOption('고등학교');
  await page.locator('#academicYear').fill('2027');
  await page.locator('#next').click();
  await page.locator('#subjects').fill('과학');
  await page.locator('#classes').fill('1학년 1반');
  await page.locator('#homeroom-no').check();
  await page.locator('#notionLocation-help').check();
  await page.locator('#sharing-private').check();
  await page.locator('#next').click();
  await page.locator('#modules-none').click();
  await page.locator('#forms-none').click();
  await page.locator('#next').click();
  await page.locator('#timetable-empty').check();
  await page.locator('#periods').fill('8');
  await page.locator('#neis-later').check();
  await page.locator('#next').click();
  await page.locator('#demo-empty').check();
  await page.locator('#next').click();
  await page.getByRole('link', { name: /이 설정으로 네 화면/ }).click();
  await expect(page.locator('#preview-shell')).toBeVisible();
}

test('a recommended preset keeps custom columns and exports all four pages', async ({ page }) => {
  await page.goto('/preview');
  await editWorkWidths(page);
  await page.getByRole('link', { name: '기능 · 예시', exact: true }).click();
  await page.locator('#workflow-picker-preset-recommended').click();
  await page.getByRole('link', { name: '배치 미리보기', exact: true }).click();
  await expectWorkWidths(page, [50, 50]);
  await expect(page.locator('#preview-module-assessment')).toBeChecked();
  await expect(page.locator('#preview-error')).toBeHidden();

  for (const tab of await page.locator('#preview-tabs [role="tab"]').all()) {
    await tab.click();
    const panelId = await tab.getAttribute('aria-controls');
    await expect(page.locator('#' + panelId)).toBeVisible();
  }
  await page.locator('#review-confirm').check();
  const downloaded = page.waitForEvent('download');
  await page.locator('#download-bundle').click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toBe('reviewed-layout.json');
  const bundle = JSON.parse(await readFile(await file.path(), 'utf8'));
  expect(Object.keys(bundle.pages).sort()).toEqual(['classroom', 'home', 'planning', 'teaching']);
  expect(bundle.selection.workflows.preset).toBe('recommended');
  const work = bundle.layout_overrides.home.find(row => row.id === 'work');
  expect(work.columns.map(column => column.ratio)).toEqual([50, 50]);
  expect(bundle.pages.home.sections.assessment).toBeDefined();
});

test('reload restores the latest layout and options after arriving from setup', async ({ page }) => {
  await openPreviewFromQuestionnaire(page);
  await page.getByText('양식 · 시간표 설정', { exact: true }).click();
  const periods = page.getByLabel('하루에 표시할 교시');
  await expect(periods).toHaveValue('8');
  await periods.fill('6');
  await periods.press('Tab');
  await editWorkWidths(page);
  const sectionOrder = () => page.locator('#notebook-home .notion-section').evaluateAll(sections => sections.map(section => section.dataset.section));
  const originalOrder = await sectionOrder();
  await page.locator('#move-handle-home-matrix').click();
  await page.locator('#move-target').selectOption('briefing');
  await page.locator('#move-position').selectOption('row-before');
  await page.getByRole('button', { name: '옮기기', exact: true }).click();
  const editedOrder = await sectionOrder();
  expect(editedOrder).not.toEqual(originalOrder);
  expect(editedOrder.indexOf('matrix')).toBe(editedOrder.indexOf('briefing') - 1);
  await page.reload();
  await expectWorkWidths(page, [50, 50]);
  expect(await sectionOrder()).toEqual(editedOrder);
  await expect(periods).toHaveValue('6');
});

test('refresh and new preset handoffs preserve free text and per-feature drafts', async ({ page }) => {
  await page.goto('/features');
  await page.getByRole('link', { name: '기존 수첩에 추가' }).click();
  const goal = '학생별 상담 이력을 최신순으로 보여주세요';
  const details = '완료된 상담 기록도 보존해 주세요.';
  await page.locator('#goal').fill(goal);
  await page.locator('#fine-tune summary').click();
  await page.locator('#location').selectOption('classroom');
  await page.locator('#view').selectOption('list');
  await page.locator('#fields').fill('상담일과 후속 조치');
  await page.locator('#details').fill(details);
  await page.reload();
  await expect(page.locator('#goal')).toHaveValue(goal);
  await expect(page.locator('#details')).toHaveValue(details);
  await expect(page.locator('#location')).toHaveValue('classroom');
  await expect(page.locator('#view')).toHaveValue('list');
  await expect(page.locator('#fields')).toHaveValue('상담일과 후속 조치');
  await page.locator('#feature-weekly-tasks').check();
  await page.locator('#goal').fill('금요일까지의 업무');
  await page.reload();
  await expect(page.locator('#feature-weekly-tasks')).toBeChecked();
  await expect(page.locator('#goal')).toHaveValue('금요일까지의 업무');
  await page.getByRole('link', { name: '기능 · 예시', exact: true }).click();
  await page.locator('#workflow-picker-preset-recommended').click();
  await page.getByRole('link', { name: '기존 수첩에 추가' }).click();
  await expect(page.locator('#goal')).toHaveValue(goal);
  await page.locator('#generate-prompt').click();
  const prompt = page.locator('#edit-prompt-output');
  await expect(prompt).toHaveValue(/추천 설정/);
  expect(await prompt.inputValue()).toContain(details);
});

test('student history and collection use the same submission record', async ({ page }) => {
  await page.goto('/features');
  const history = page.locator('#example-student_history');
  const collection = page.locator('#example-submissions');
  await history.getByRole('button', { name: /미제출/ }).click();
  const pending = history.getByRole('table');
  await expect(pending).not.toContainText('체험학습 동의서');
  await history.getByRole('combobox').selectOption('가상학생 B');
  await expect(pending).toContainText('체험학습 동의서');
  await expect(pending).toContainText('미제출');
  const status = collection.getByLabel('가상학생 B 제출 상태');
  await status.selectOption('제출');
  await expect(history.getByRole('combobox')).toHaveValue('가상학생 B');
  await expect(pending).not.toContainText('체험학습 동의서');
  await expect(collection).toContainText('66.7%');
  await status.selectOption('보완 요청');
  await expect(pending).toContainText('보완 요청');
  await expect(collection).toContainText('33.3%');
});

test('keyboard focus enters the column editor and returns to the same row after a redraw', async ({ page }) => {
  await page.goto('/preview');
  const trigger = workRowButton(page);
  await trigger.focus();
  await page.keyboard.press('Enter');
  const panel = page.locator('#columns-panel');
  await expect(panel).toBeVisible();
  await expect(panel.locator(':focus')).toHaveCount(1);
  await page.keyboard.press('Tab');
  await expect(panel.locator(':focus')).toHaveCount(1);
  await page.getByRole('button', { name: '너비 같게' }).press('Enter');
  await expectWorkWidths(page, [50, 50]);
  await page.getByRole('button', { name: '열 편집 닫기' }).press('Enter');
  await expect(panel).toBeHidden();
  await expect(trigger).toBeFocused();
});
