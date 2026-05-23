// TASK-PC3-A — KbApplicationModal tests.
//
// Five validation cases + happy path. Mocks `submitApplication` so no
// fetch ever fires; rejections use real Promise.reject (守
// `feedback_tdd_mock_vs_real_api` SOP 10).

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return {
    ...actual,
    submitApplication: vi.fn(),
  };
});

import { notification } from "antd";
import {
  KbApplicationModal,
  MAX_FILE_SIZE_BYTES,
  extractExtension,
  validateFileList,
} from "@/pages/MyKbPage/components/KbApplicationModal";
import { ApiError, submitApplication } from "@/lib/api";

const mockSubmit = vi.mocked(submitApplication);

beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: (query: string): MediaQueryList =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addListener: () => {},
          removeListener: () => {},
          addEventListener: () => {},
          removeEventListener: () => {},
          dispatchEvent: () => false,
        }) as MediaQueryList,
    });
  }
});

beforeEach(() => {
  mockSubmit.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// Antd Modal renders into a portal; data-testid on Upload may not survive
// cross-test cleanup, so look up the file input scoped to the visible
// dialog instead. The dialog always has role="dialog" and is unique
// within a single test (one modal open at a time).
function getCurrentDialog(): HTMLElement {
  // Use getAllByRole + .at(-1) so we explicitly grab the most-recent
  // dialog even if jsdom retains stale portal shells from prior tests.
  const dialogs = screen.getAllByRole("dialog");
  const dialog = dialogs[dialogs.length - 1];
  if (!dialog) throw new Error("no dialog visible");
  return dialog;
}

function getDialogFileInput(): HTMLInputElement {
  const input = getCurrentDialog().querySelector(
    'input[type="file"]',
  ) as HTMLInputElement | null;
  if (!input) throw new Error("no file input inside dialog");
  return input;
}

function getDialogSubmitButton(): HTMLElement {
  const btns = within(getCurrentDialog()).getAllByRole("button", {
    name: /提\s?交/,
  });
  return btns[btns.length - 1]!;
}

function makeUploadFile(name: string, size: number) {
  // Antd UploadFile shape — we only populate the fields validateFileList
  // and the submit path read (`name`, `size`, `originFileObj`).
  const blob = new Blob(["x"], { type: "application/octet-stream" });
  // Tag the blob with a name so File-like checks in real code work; tests
  // only need .size and .name semantics so a plain Blob is fine.
  const file = new File([blob], name, { type: "application/octet-stream" });
  Object.defineProperty(file, "size", { value: size });
  return {
    uid: `uid-${name}`,
    name,
    size,
    originFileObj: file,
  };
}

function renderModal(props: Partial<React.ComponentProps<typeof KbApplicationModal>> = {}) {
  const onClose = vi.fn();
  const onSubmitted = vi.fn();
  const utils = render(
    <KbApplicationModal
      open
      onClose={onClose}
      onSubmitted={onSubmitted}
      {...props}
    />,
  );
  return { ...utils, onClose, onSubmitted };
}

describe("validateFileList()", () => {
  it("returns null when files are within all limits", () => {
    const files = [makeUploadFile("a.pdf", 1024)] as never[];
    expect(validateFileList(files)).toBeNull();
  });

  it("rejects an empty file list", () => {
    expect(validateFileList([])).toBe("请选择至少 1 个文件");
  });

  it("rejects > 10 files (Q2-B MAX_FILES_PER_APPLICATION)", () => {
    const files = Array.from({ length: 11 }, (_, i) =>
      makeUploadFile(`f${i}.txt`, 100),
    ) as never[];
    expect(validateFileList(files)).toBe("最多上传 10 个文件");
  });

  it("rejects a > 50 MB file (Q2-B MAX_FILE_SIZE_BYTES)", () => {
    const files = [makeUploadFile("big.pdf", MAX_FILE_SIZE_BYTES + 1)] as never[];
    expect(validateFileList(files)).toBe("单文件不能超过 50 MB");
  });

  it("rejects an unsupported extension", () => {
    const files = [makeUploadFile("evil.exe", 100)] as never[];
    expect(validateFileList(files)).toBe("不支持的文件格式 .exe");
  });

  it("rejects an extensionless file", () => {
    const files = [makeUploadFile("noext", 100)] as never[];
    expect(validateFileList(files)).toBe("不支持的文件格式 .");
  });
});

describe("extractExtension()", () => {
  it("returns lower-cased extension after the last dot", () => {
    expect(extractExtension("foo.PDF")).toBe("pdf");
    expect(extractExtension("a.b.docx")).toBe("docx");
    expect(extractExtension("noext")).toBe("");
  });
});

describe("<KbApplicationModal>", () => {
  it("AC#2(happy) — required+file → submitApplication + onSubmitted + onClose", async () => {
    mockSubmit.mockResolvedValueOnce({
      id: "new-app",
      user_id: "u1",
      kb_name_suggested: "HR 政策 KB",
      description: null,
      status: "pending",
      fastgpt_dataset_id: null,
      dify_app_id: null,
      admin_processed_by: null,
      admin_processed_at: null,
      rejection_reason: null,
      created_at: new Date().toISOString(),
    });
    const successSpy = vi
      .spyOn(notification, "success")
      .mockImplementation(() => undefined);

    const { onClose, onSubmitted } = renderModal();
    // Fill required field.
    const nameInput = within(getCurrentDialog()).getByPlaceholderText(
      /例如：研发部 OKR 知识库/,
    ) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "HR 政策 KB" } });

    const fileInput = getDialogFileInput();
    const f = new File(["hello"], "doc.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [f] } });
    });

    // Submit.
    fireEvent.click(getDialogSubmitButton());

    await waitFor(() => {
      expect(mockSubmit).toHaveBeenCalledTimes(1);
    });
    const arg = mockSubmit.mock.calls[0]![0];
    expect(arg.kb_name_suggested).toBe("HR 政策 KB");
    expect(arg.files).toHaveLength(1);
    expect(arg.files[0]!.name).toBe("doc.pdf");
    expect(successSpy).toHaveBeenCalledTimes(1);
    expect(onSubmitted).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("AC#2(missing-name) — empty kb_name_suggested → form error, no submit", async () => {
    renderModal();
    const dialog = getCurrentDialog();
    // Attach a valid file so the file-validation path doesn't shadow
    // the required-name error.
    const fileInput = getDialogFileInput();
    const f = new File(["x"], "a.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [f] } });
    });
    fireEvent.click(getDialogSubmitButton());
    await waitFor(() => {
      expect(
        within(dialog).getByText("请填写知识库建议名称"),
      ).toBeInTheDocument();
    });
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("AC#2(no-files) — required name but empty file list → file error, no submit", async () => {
    renderModal();
    const dialog = getCurrentDialog();
    const nameInput = within(dialog).getByPlaceholderText(
      /例如：研发部 OKR 知识库/,
    ) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "x" } });
    fireEvent.click(getDialogSubmitButton());
    await waitFor(() => {
      expect(
        within(dialog).getByText("请选择至少 1 个文件"),
      ).toBeInTheDocument();
    });
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("AC#2(bad-ext) — unsupported extension → file error, no submit", async () => {
    renderModal();
    const dialog = getCurrentDialog();
    const nameInput = within(dialog).getByPlaceholderText(
      /例如：研发部 OKR 知识库/,
    ) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "x" } });
    const fileInput = getDialogFileInput();
    const f = new File(["x"], "evil.exe", { type: "application/octet-stream" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [f] } });
    });
    fireEvent.click(getDialogSubmitButton());
    await waitFor(() => {
      expect(
        within(dialog).getByText("不支持的文件格式 .exe"),
      ).toBeInTheDocument();
    });
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("AC#2(422) — backend 422 → form error inline (kb_name_suggested), notification.error not fired", async () => {
    mockSubmit.mockRejectedValueOnce(
      new ApiError(422, undefined, "kb_name_suggested 最长 200 字符"),
    );
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderModal();
    const dialog = getCurrentDialog();
    const nameInput = within(dialog).getByPlaceholderText(
      /例如：研发部 OKR 知识库/,
    ) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "x".repeat(50) } });
    const fileInput = getDialogFileInput();
    const f = new File(["x"], "a.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [f] } });
    });
    fireEvent.click(getDialogSubmitButton());

    await waitFor(() => {
      expect(
        within(dialog).getByText("kb_name_suggested 最长 200 字符"),
      ).toBeInTheDocument();
    });
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("AC#2(500) — non-422 reject → notification.error '提交失败'", async () => {
    mockSubmit.mockRejectedValueOnce(
      new ApiError(500, undefined, "upstream broken"),
    );
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderModal();
    const dialog = getCurrentDialog();
    const nameInput = within(dialog).getByPlaceholderText(
      /例如：研发部 OKR 知识库/,
    ) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "ok name" } });
    const fileInput = getDialogFileInput();
    const f = new File(["x"], "a.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [f] } });
    });
    fireEvent.click(getDialogSubmitButton());

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({ message: "提交失败" }),
    );
  });
});
