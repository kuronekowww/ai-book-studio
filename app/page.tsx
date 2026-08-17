"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";

type View = "library" | "projects" | "prompts" | "runs" | "settings";
type BookType = "narrative" | "non_narrative";
type ProjectModelStage =
  | "mind_map"
  | "album_outline"
  | "episode_outline"
  | "episode_draft"
  | "episode_final";

type EffectiveModel = {
  model_id: string;
  label: string;
  model: string;
  provider: string;
  follows_global: boolean;
};

type Book = {
  id: string;
  title: string;
  author: string;
  book_type: BookType;
  filename: string;
  status: string;
  source_type: string;
  section_count?: number;
  theme_count?: number;
  article_count?: number;
  knowledge_count?: number;
  sections?: Section[];
  knowledge?: KnowledgeItem[];
  mind_map?: { content: string } | null;
  chapter_analyses?: ChapterAnalysis[];
  fragment_set?: { id: string; version: number } | null;
  fragment_count?: number;
  analysis_model_id?: string | null;
  effective_analysis_model?: EffectiveModel;
};

type Section = {
  id: string;
  parent_id: string | null;
  level: number;
  position: number;
  title: string;
  content: string;
  kind: string;
  status: string;
  analysis_enabled: boolean;
  analysis_exclusion_reason: string;
};

type ChapterAnalysis = {
  id: string;
  root_section_id: string;
  version: number;
  status: string;
  chapter_title: string;
  rendered_markdown: string;
  compressed_markdown: string;
  provider: string;
  model: string;
  fragment_set_id: string | null;
  validation_issues_json: {
    asset_type: string;
    title: string;
    error: string;
    source_content_indexes: string[];
  }[];
  valid_item_count: number;
  invalid_item_count: number;
  created_at: string;
};

type KnowledgeItem = {
  id: string;
  kind: string;
  title: string;
  body: string;
  source_section_ids: string[];
  source_content_indexes: string[];
  source_scheme: string;
  status: string;
};

type EvidenceFragment = {
  content_index: string;
  source_section_id: string;
  section_path_json: string[];
  text: string;
  book_position: number;
};

type EvidenceBundle = {
  knowledge_items: KnowledgeItem[];
  direct_fragments: EvidenceFragment[];
  auxiliary_fragments: EvidenceFragment[];
  legacy_sections: Section[];
};

type Project = {
  id: string;
  title: string;
  status: string;
  book_ids: string[];
  episode_count?: number;
  completed_count?: number;
  episodes?: Episode[];
  album_special_requirements?: string;
  desired_episode_count?: number | null;
  episode_word_count_min?: number;
  episode_word_count_max?: number;
  episode_count_notice?: string;
  model_overrides?: Record<ProjectModelStage, string | null>;
  effective_models?: Record<ProjectModelStage, EffectiveModel>;
  global_model?: EffectiveModel;
};

type Episode = {
  id: string;
  project_id: string;
  position: number;
  title: string;
  content_type: string;
  style: string;
  content_framework: string;
  section_identifier: string;
  status: string;
  source_section_ids: string[];
  knowledge_item_ids: string[];
  source_content_indexes: string[];
  versions?: ArtifactVersion[];
  sources?: Section[];
  evidence?: EvidenceBundle;
};

type ArtifactVersion = {
  id: string;
  stage: "outline" | "draft" | "final";
  version: number;
  content: string;
  provider: string;
  model: string;
  prompt_version: string;
  prompt_version_id?: string | null;
  prompt_system_version_id?: string | null;
  author_type: "model" | "human";
  created_at: string;
};

type SettingsStatus = {
  provider: string;
  model: string;
  current_model_id: string | null;
  selection_source: "local" | "environment";
  api_key_configured: boolean;
  data_dir: string;
  available_models: ModelOption[];
};

type ModelOption = {
  id: string;
  label: string;
  model: string;
  provider: string;
};

type PromptStage =
  | "mind_map"
  | "album_module_plan"
  | "album_outline"
  | "episode_outline"
  | "episode_draft"
  | "episode_final";

type PromptTemplateConfig = {
  stage_key: PromptStage;
  label: string;
  source_scope: "system" | "global" | "project";
  source_label: string;
  user_template: string;
  prompt_version_id: string;
  version: number;
  system_version_id: string;
  system_version: string;
  allowed_placeholders: Record<string, string>;
  required_placeholders: string[];
  required_placeholder_groups: string[][];
  has_project_override: boolean;
  has_global_override: boolean;
};

type PromptHistoryVersion = {
  id: string;
  scope: "global" | "project";
  project_id: string | null;
  version: number;
  user_template: string;
  source_version_id: string | null;
  created_at: string;
};

type PromptPreview = {
  rendered_user_template: string;
  protected_suffix: string;
  source_label: string;
  truncated: boolean;
  input_materials: PromptInputMaterial[];
};

type PromptInputMaterial = {
  key: string;
  label: string;
  source: string;
  character_count: number;
  compressed: boolean;
  content: string;
};

type PromptModuleOption = {
  run_id: string;
  module_key: string;
  position: number;
  title: string;
  chapter_ids: string[];
  character_count: number;
};

type WorkflowRun = {
  id: string;
  scope_type: string;
  scope_id: string;
  stage: string;
  current_stage: string;
  status: "pending" | "running" | "succeeded" | "partial_failed" | "failed" | "cancelled";
  message: string;
  progress_current: number;
  progress_total: number;
  started_at?: string | null;
  finished_at?: string | null;
  heartbeat_at?: string | null;
  attempt?: number;
  scope_label?: string;
  project_id?: string;
  book_id?: string;
  reused?: boolean;
  parent_run_id?: string | null;
  error_stage?: string;
  position?: number;
  metadata_json?: {
    model_id?: string;
    model?: string;
    provider?: string;
    stage_model_ids?: Partial<Record<"outline" | "draft" | "final", string>>;
    stage_prompt_locks?: Partial<
      Record<
        "outline" | "draft" | "final",
        { prompt_version_id: string; system_version_id: string }
      >
    >;
    stages?: Record<
      string,
      {
        status: string;
        message?: string;
        updated_at?: string;
        output?: Record<string, unknown>;
      }
    >;
  };
  created_at: string;
  updated_at: string;
};

type RunOutput = {
  id?: string;
  stage: string;
  artifact_type: string;
  label: string;
  content: string;
  version?: number;
  provider?: string;
  model?: string;
  created_at?: string;
  episode_count?: number;
  planning_artifact_type?: string;
  module_key?: string;
  status?: string;
  error_message?: string;
  position?: number;
};

type BatchChild = WorkflowRun & {
  episode_title: string;
  episode_status: string;
};

type BatchRun = WorkflowRun & {
  children: BatchChild[];
  summary: {
    total: number;
    completed: number;
    failed: number;
    running: number;
    pending: number;
    concurrency: number;
  };
};

const statusLabels: Record<string, string> = {
  segment_review: "待确认章节",
  ready_to_analyze: "待拆书",
  analyzed: "知识已入库",
  analysis_partial: "部分章节已完成",
  analysis_partial_failed: "部分拆解失败",
  outline_review: "待确认大纲",
  production: "等待生产",
  ready: "等待生产",
  producing: "批量生产中",
  review: "待审核",
  approved: "已确认",
  partial_failed: "部分失败",
  queued: "排队中",
  generating_outline: "生成细纲",
  generating_draft: "生成初稿",
  generating_final: "口语化调整",
  matching_sources: "匹配本集原文",
  failed: "生成失败",
  completed: "已完成",
};

const stageLabels = {
  match_episode_sources: "匹配本集原文",
  outline: "声音细纲",
  draft: "声音初稿",
  final: "声音终稿",
};

const workflowStageLabels: Record<string, string> = {
  prepare_chapters: "准备章节",
  analyze_chapters: "逐章拆书",
  finalize_book: "汇总书籍知识",
  prepare_analysis: "准备完整拆书稿",
  prepare_chapter_catalog: "准备轻量章节目录",
  generate_mind_map: "生成思维导图",
  design_album_modules: "设计全书知识模块",
  expand_album_modules: "分模块生成专辑大纲",
  structure_album_outline: "整理专辑大纲页面数据",
  expand_album_module: "生成当前模块大纲",
  match_episode_sources: "匹配本集原文",
  save_project_outline: "校验并保存专辑大纲",
  episode_generation: "批量生产声音",
  book_analysis: "章节拆书",
  ...stageLabels,
};

const projectModelStageLabels: Record<ProjectModelStage, string> = {
  mind_map: "思维导图",
  album_outline: "专辑大纲",
  episode_outline: "声音细纲",
  episode_draft: "声音初稿",
  episode_final: "声音终稿",
};

const projectModelStages = Object.keys(
  projectModelStageLabels,
) as ProjectModelStage[];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let message = "请求失败";
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      message = `${message}（${response.status}）`;
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function formatCount(value: number | undefined) {
  return new Intl.NumberFormat("zh-CN").format(value ?? 0);
}

function countSpokenWords(text: string) {
  return (
    text.match(
      /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[0-9]|[A-Za-z]+(?:['’\-][A-Za-z]+)*/g,
    )?.length || 0
  );
}

function projectFinalsFilename(title: string) {
  const safeTitle = title
    .replace(/[\/\\:*?"<>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return `${safeTitle || "讲书专辑"}_全部终稿.md`;
}

export default function Home() {
  const [view, setView] = useState<View>("library");
  const [books, setBooks] = useState<Book[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [selectedEpisode, setSelectedEpisode] = useState<Episode | null>(null);
  const [batch, setBatch] = useState<BatchRun | null>(null);
  const [settings, setSettings] = useState<SettingsStatus | null>(null);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [backendOnline, setBackendOnline] = useState(false);
  const [vaultPath, setVaultPath] = useState("");
  const [promptDirty, setPromptDirty] = useState(false);
  const [workspaceRestored, setWorkspaceRestored] = useState(false);
  const previousActiveRuns = useRef<Map<string, WorkflowRun>>(new Map());

  const refresh = useCallback(async () => {
    try {
      const [bookList, projectList, settingsStatus, runList] = await Promise.all([
        request<Book[]>("/api/books"),
        request<Project[]>("/api/projects"),
        request<SettingsStatus>("/api/settings/status"),
        request<WorkflowRun[]>("/api/runs"),
      ]);
      setBooks(bookList);
      setProjects(projectList);
      setSettings(settingsStatus);
      setRuns(runList);
      setBackendOnline(true);
      setError("");
    } catch (caught) {
      setBackendOnline(false);
      setError(caught instanceof Error ? caught.message : "本地服务未连接");
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      request<Book[]>("/api/books"),
      request<Project[]>("/api/projects"),
      request<SettingsStatus>("/api/settings/status"),
      request<WorkflowRun[]>("/api/runs"),
    ])
      .then(async ([bookList, projectList, settingsStatus, runList]) => {
        if (!active) return;
        const savedView = window.localStorage.getItem("ai-book-studio:view");
        const savedBookId = window.localStorage.getItem("ai-book-studio:book");
        const savedProjectId = window.localStorage.getItem("ai-book-studio:project");
        const savedEpisodeId = window.localStorage.getItem("ai-book-studio:episode");
        const restoredBook = savedBookId && bookList.some((item) => item.id === savedBookId)
          ? await request<Book>(`/api/books/${savedBookId}`)
          : null;
        const restoredProject =
          savedProjectId && projectList.some((item) => item.id === savedProjectId)
            ? await request<Project>(`/api/projects/${savedProjectId}`)
            : null;
        const restoredEpisode =
          restoredProject &&
          savedEpisodeId &&
          restoredProject.episodes?.some((item) => item.id === savedEpisodeId)
            ? await request<Episode>(`/api/episodes/${savedEpisodeId}`)
            : null;
        const restoredBatch = restoredProject
          ? await request<BatchRun | null>(
              `/api/projects/${restoredProject.id}/batch`,
            )
          : null;
        if (!active) return;
        setBooks(bookList);
        setProjects(projectList);
        setSettings(settingsStatus);
        setRuns(runList);
        setSelectedBook(restoredBook);
        setSelectedProject(restoredProject);
        setSelectedEpisode(restoredEpisode);
        setBatch(restoredBatch);
        if (
          savedView &&
          ["library", "projects", "prompts", "runs", "settings"].includes(savedView)
        ) {
          setView(savedView as View);
        }
        previousActiveRuns.current = new Map(
          runList
            .filter((run) => ["pending", "running"].includes(run.status))
            .map((run) => [run.id, run]),
        );
        setBackendOnline(true);
        setWorkspaceRestored(true);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setBackendOnline(false);
        setError(caught instanceof Error ? caught.message : "本地服务未连接");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const bookId = selectedBook?.id;
    const projectId = selectedProject?.id;
    const episodeId = selectedEpisode?.id;
    let polling = false;
    const interval = window.setInterval(() => {
      if (polling) return;
      polling = true;
      request<WorkflowRun[]>("/api/runs?limit=100")
        .then(async (nextRuns) => {
          const activeRuns = nextRuns.filter((run) =>
            ["pending", "running"].includes(run.status),
          );
          const previous = previousActiveRuns.current;
          const relevantRuns = [
            ...activeRuns,
            ...Array.from(previous.values()).filter(
              (run) => !activeRuns.some((item) => item.id === run.id),
            ),
          ];
          const refreshBook = Boolean(
            bookId &&
              relevantRuns.some(
                (run) =>
                  (run.scope_type === "book_analysis_batch" &&
                    run.scope_id === bookId) ||
                  run.book_id === bookId,
              ),
          );
          const refreshProject = Boolean(
            projectId &&
              relevantRuns.some(
                (run) =>
                  (["project_generation", "project_batch"].includes(
                    run.scope_type,
                  ) &&
                    run.scope_id === projectId) ||
                  run.project_id === projectId,
              ),
          );
          const refreshEpisode = Boolean(
            episodeId &&
              relevantRuns.some(
                (run) =>
                  (run.scope_type === "episode" &&
                    run.scope_id === episodeId) ||
                  (run.scope_type === "project_batch" &&
                    run.scope_id === projectId),
              ),
          );
          const [nextBook, nextProject, nextEpisode, nextBatch] =
            await Promise.all([
              refreshBook && bookId
                ? request<Book>(`/api/books/${bookId}`)
                : Promise.resolve(null),
              refreshProject && projectId
                ? request<Project>(`/api/projects/${projectId}`)
                : Promise.resolve(null),
              refreshEpisode && episodeId
                ? request<Episode>(`/api/episodes/${episodeId}`)
                : Promise.resolve(null),
              refreshProject && projectId
                ? request<BatchRun | null>(`/api/projects/${projectId}/batch`)
                : Promise.resolve(null),
            ]);
          setRuns(nextRuns);
          if (nextBook) setSelectedBook(nextBook);
          if (nextProject) setSelectedProject(nextProject);
          if (nextEpisode) setSelectedEpisode(nextEpisode);
          if (refreshProject) setBatch(nextBatch);
          previousActiveRuns.current = new Map(
            activeRuns.map((run) => [run.id, run]),
          );
          setBackendOnline(true);
        })
        .catch((caught: unknown) => {
          setBackendOnline(false);
          setError(caught instanceof Error ? caught.message : "任务状态刷新失败");
        })
        .finally(() => {
          polling = false;
        });
    }, 1000);
    return () => window.clearInterval(interval);
  }, [selectedBook?.id, selectedEpisode?.id, selectedProject?.id]);

  useEffect(() => {
    if (!workspaceRestored) return;
    window.localStorage.setItem("ai-book-studio:view", view);
    if (selectedBook?.id) {
      window.localStorage.setItem("ai-book-studio:book", selectedBook.id);
    } else {
      window.localStorage.removeItem("ai-book-studio:book");
    }
    if (selectedProject?.id) {
      window.localStorage.setItem(
        "ai-book-studio:project",
        selectedProject.id,
      );
    } else {
      window.localStorage.removeItem("ai-book-studio:project");
    }
    if (selectedEpisode?.id) {
      window.localStorage.setItem(
        "ai-book-studio:episode",
        selectedEpisode.id,
      );
    } else {
      window.localStorage.removeItem("ai-book-studio:episode");
    }
  }, [
    selectedBook?.id,
    selectedEpisode?.id,
    selectedProject?.id,
    view,
    workspaceRestored,
  ]);

  const runAction = async (
    label: string,
    action: () => Promise<string | void>,
  ) => {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const message = await action();
      setNotice(message || `${label}完成`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `${label}失败`);
    } finally {
      setBusy("");
    }
  };

  const registerRun = (run: WorkflowRun) => {
    setRuns((current) => [
      run,
      ...current.filter((item) => item.id !== run.id),
    ]);
    if (["pending", "running"].includes(run.status)) {
      previousActiveRuns.current.set(run.id, run);
    }
  };

  const openBook = async (bookId: string) => {
    await runAction("载入书籍", async () => {
      setSelectedBook(await request<Book>(`/api/books/${bookId}`));
      setView("library");
    });
  };

  const openProject = async (projectId: string) => {
    await runAction("载入项目", async () => {
      const [project, latestBatch] = await Promise.all([
        request<Project>(`/api/projects/${projectId}`),
        request<BatchRun | null>(`/api/projects/${projectId}/batch`),
      ]);
      setSelectedProject(project);
      setBatch(latestBatch);
      setSelectedEpisode(null);
      setView("projects");
    });
  };

  const openEpisode = async (episodeId: string) => {
    await runAction("载入声音", async () => {
      setSelectedEpisode(await request<Episode>(`/api/episodes/${episodeId}`));
    });
  };

  const uploadBook = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get("file");
    if (!(file instanceof File) || !file.name) {
      setError("请选择 EPUB、TXT 或 Markdown 文件");
      return;
    }
    await runAction("导入书籍", async () => {
      const result = await request<Book>("/api/books/import", {
        method: "POST",
        body: data,
      });
      form.reset();
      await refresh();
      setSelectedBook(result);
    });
  };

  const confirmSections = () =>
    selectedBook &&
    runAction("确认章节", async () => {
      setSelectedBook(
        await request<Book>(`/api/books/${selectedBook.id}/confirm`, {
          method: "POST",
        }),
      );
      await refresh();
    });

  const analyzeBook = () =>
    selectedBook &&
    runAction("启动拆书任务", async () => {
      const run = await request<WorkflowRun>(
        `/api/books/${selectedBook.id}/analyze`,
        { method: "POST" },
      );
      registerRun(run);
      return run.reused ? "已有拆书任务正在执行" : "拆书任务已进入后台";
    });

  const retryChapter = (sectionId: string) =>
    selectedBook &&
    runAction("启动章节重跑", async () => {
      const run = await request<WorkflowRun>(
        `/api/books/${selectedBook.id}/chapters/${sectionId}/analyze`,
        { method: "POST" },
      );
      registerRun(run);
      return run.reused ? "已有拆书任务正在执行" : "章节重跑已进入后台";
    });

  const updateBookType = (bookType: BookType) =>
    selectedBook &&
    runAction("更新书籍类型", async () => {
      setSelectedBook(
        await request<Book>(`/api/books/${selectedBook.id}/type`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ book_type: bookType }),
        }),
      );
      await refresh();
    });

  const updateBookModel = (modelId: string | null) =>
    selectedBook &&
    runAction("保存拆书模型", async () => {
      const config = await request<{
        analysis_model_id: string | null;
        effective_analysis_model: EffectiveModel;
      }>(`/api/books/${selectedBook.id}/model`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: modelId }),
      });
      setSelectedBook({ ...selectedBook, ...config });
    });

  const saveSections = (sections: Section[]) =>
    selectedBook &&
    runAction("保存章节调整", async () => {
      setSelectedBook(
        await request<Book>(`/api/books/${selectedBook.id}/sections`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sections }),
        }),
      );
      await refresh();
    });

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const title = String(data.get("title") || "").trim();
    const bookId = String(data.get("book_id") || "");
    if (!title || !bookId) {
      setError("请填写项目名称并选择书籍");
      return;
    }
    await runAction("创建内容项目", async () => {
      const project = await request<Project>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, book_id: bookId }),
      });
      setSelectedProject(project);
      setSelectedEpisode(null);
      await refresh();
    });
  };

  const confirmOutline = () =>
    selectedProject &&
    runAction("确认专辑大纲", async () => {
      const project = await request<Project>(
        `/api/projects/${selectedProject.id}/confirm`,
        { method: "POST" },
      );
      setSelectedProject(project);
      setBatch(null);
      await refresh();
    });

  const generateProjectOutline = (
    specialRequirements: string,
    desiredEpisodeCount: number | null,
    episodeWordCountMin: number,
    episodeWordCountMax: number,
  ) =>
    selectedProject &&
    runAction("启动专辑规划任务", async () => {
      const run = await request<WorkflowRun>(
        `/api/projects/${selectedProject.id}/generate-outline`,
        {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          album_special_requirements: specialRequirements,
          desired_episode_count: desiredEpisodeCount,
          episode_word_count_min: episodeWordCountMin,
          episode_word_count_max: episodeWordCountMax,
        }),
        },
      );
      registerRun(run);
      setSelectedEpisode(null);
      return run.reused
          ? "已有专辑规划任务正在执行"
          : "思维导图与专辑大纲已进入后台生成";
    });

  const saveOutline = (episodes: Episode[]) =>
    selectedProject &&
    runAction("保存专辑大纲", async () => {
      setSelectedProject(
        await request<Project>(
          `/api/projects/${selectedProject.id}/episodes`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ episodes }),
          },
        ),
      );
      setSelectedEpisode(null);
      setBatch(null);
      await refresh();
    });

  const updateProjectModel = (
    stage: ProjectModelStage,
    modelId: string | null,
  ) =>
    selectedProject &&
    runAction("保存环节模型", async () => {
      const config = await request<{
        model_overrides: Record<ProjectModelStage, string | null>;
        effective_models: Record<ProjectModelStage, EffectiveModel>;
      }>(`/api/projects/${selectedProject.id}/models/${stage}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: modelId }),
      });
      setSelectedProject({ ...selectedProject, ...config });
    });

  const generateAll = () =>
    selectedProject &&
    runAction("启动整张专辑生产", async () => {
      const result = await request<BatchRun>(
        `/api/projects/${selectedProject.id}/generate-all`,
        { method: "POST" },
      );
      setBatch(result);
      registerRun(result);
      setSelectedProject(
        await request<Project>(`/api/projects/${selectedProject.id}`),
      );
      return `已启动 ${result.summary.total} 条声音，最多 5 条并行生产`;
    });

  const exportProjectFinals = () =>
    selectedProject &&
    runAction("导出全部终稿", async () => {
      const response = await fetch(
        `${API_BASE}/api/projects/${selectedProject.id}/finals/export`,
      );
      if (!response.ok) {
        let message = "导出全部终稿失败";
        try {
          const payload = await response.json();
          message = payload.detail || message;
        } catch {
          message = `${message}（${response.status}）`;
        }
        throw new Error(message);
      }
      const markdown = await response.text();
      const blob = new Blob([markdown], {
        type: "text/markdown;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = projectFinalsFilename(selectedProject.title);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      return "已导出全部终稿";
    });

  const generateEpisode = (fromStage: "outline" | "draft" | "final") =>
    selectedEpisode &&
    runAction(fromStage === "outline" ? "启动声音生成" : `启动${stageLabels[fromStage]}重跑`, async () => {
      const run = await request<WorkflowRun>(
        `/api/episodes/${selectedEpisode.id}/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ from_stage: fromStage }),
        },
      );
      registerRun(run);
      return run.reused ? "这条声音正在生成" : "声音任务已进入后台";
    });

  const retryAlbumModule = (runId: string, moduleKey: string) =>
    runAction("重跑专辑模块", async () => {
      const run = await request<WorkflowRun>(
        `/api/runs/${runId}/modules/${moduleKey}/retry`,
        { method: "POST" },
      );
      registerRun(run);
      return `${moduleKey} 已重新进入后台生成`;
    });

  const saveFinalVersion = async (content: string): Promise<boolean> => {
    if (!selectedEpisode) return false;
    let saved = false;
    await runAction("保存人工终稿", async () => {
      const result = await request<Episode>(
        `/api/episodes/${selectedEpisode.id}/final-versions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        },
      );
      setSelectedEpisode(result);
      saved = true;
      await refresh();
    });
    return saved;
  };

  const cancelRun = (runId: string) =>
    runAction("取消任务", async () => {
      await request(`/api/runs/${runId}/cancel`, { method: "POST" });
      await refresh();
    });

  const syncObsidian = () =>
    runAction("同步 Obsidian", async () => {
      const result = await request<{ changed_count: number; root: string }>(
        "/api/obsidian/sync",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            vault_path: vaultPath,
            book_id: selectedBook?.id || null,
            project_id: selectedProject?.id || null,
          }),
        },
      );
      setNotice(`已写入 ${result.changed_count} 个文件：${result.root}`);
    });

  const selectGlobalModel = async (modelId: string): Promise<boolean> => {
    let saved = false;
    await runAction("切换全局模型", async () => {
      const result = await request<SettingsStatus>("/api/settings/model", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: modelId }),
      });
      setSettings(result);
      saved = true;
    });
    return saved;
  };

  const activeRuns = runs.filter((run) =>
    ["pending", "running"].includes(run.status),
  );
  const activeTopRuns = activeRuns.filter((run) => !run.parent_run_id);
  const selectedBookRun = selectedBook
    ? activeTopRuns.find(
        (run) =>
          run.scope_type === "book_analysis_batch" &&
          run.scope_id === selectedBook.id,
      ) || null
    : null;
  const selectedProjectRun = selectedProject
    ? activeTopRuns.find(
        (run) =>
          ["project_generation", "project_batch"].includes(run.scope_type) &&
          run.scope_id === selectedProject.id,
      ) ||
        runs.find(
          (run) =>
            run.scope_type === "project_generation" &&
            run.scope_id === selectedProject.id &&
            run.status === "partial_failed",
        ) ||
        null
    : null;
  const selectedEpisodeRun = selectedEpisode
    ? activeRuns.find(
        (run) =>
          run.scope_type === "episode" &&
          run.scope_id === selectedEpisode.id,
      ) || null
    : null;

  const navItems: { key: View; label: string; hint: string }[] = [
    { key: "library", label: "书籍知识库", hint: `${books.length} 本` },
    { key: "projects", label: "内容项目", hint: `${projects.length} 个` },
    { key: "prompts", label: "提示词", hint: "6 个环节" },
    {
      key: "runs",
      label: "运行记录",
      hint: activeTopRuns.length ? `${activeTopRuns.length} 个执行中` : "正常",
    },
    { key: "settings", label: "设置与同步", hint: settings?.provider || "—" },
  ];

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">阅</div>
          <div>
            <strong>知声工坊</strong>
            <span>AI Book Studio</span>
          </div>
        </div>
        <nav className="main-nav" aria-label="主导航">
          {navItems.map((item) => (
            <button
              key={item.key}
              className={view === item.key ? "nav-item active" : "nav-item"}
              onClick={() => {
                if (
                  view === "prompts" &&
                  item.key !== "prompts" &&
                  promptDirty &&
                  !window.confirm("提示词还有未保存修改，确定离开吗？")
                ) {
                  return;
                }
                setView(item.key);
                if (item.key === "library") setSelectedProject(null);
                if (item.key === "projects") setSelectedBook(null);
              }}
            >
              <span>{item.label}</span>
              <small>{item.hint}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className={backendOnline ? "status-dot online" : "status-dot"} />
          <div>
            <strong>{backendOnline ? "本地服务已连接" : "等待本地服务"}</strong>
            <small>{settings?.model || "127.0.0.1:8000"}</small>
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">
              {view === "library" && "KNOWLEDGE LIBRARY"}
              {view === "projects" && "CONTENT STUDIO"}
              {view === "prompts" && "PROMPT WORKBENCH"}
              {view === "runs" && "WORKFLOW RUNS"}
              {view === "settings" && "LOCAL SETTINGS"}
            </p>
            <h1>
              {view === "library" && (selectedBook?.title || "书籍知识库")}
              {view === "projects" && (selectedProject?.title || "内容项目")}
              {view === "prompts" && "提示词配置"}
              {view === "runs" && "运行记录"}
              {view === "settings" && "设置与 Obsidian"}
            </h1>
          </div>
          <div className="topbar-actions">
            {busy && <span className="busy-chip">{busy}…</span>}
            <button className="quiet-button" onClick={() => void refresh()}>
              刷新
            </button>
          </div>
        </header>

        {(notice || error) && (
          <div className={error ? "notice error" : "notice"}>
            <span>{error || notice}</span>
            <button onClick={() => { setError(""); setNotice(""); }}>×</button>
          </div>
        )}

        <div className="content">
          {view === "library" &&
            (selectedBook ? (
              <BookWorkspace
                book={selectedBook}
                onBack={() => setSelectedBook(null)}
                onConfirm={() => void confirmSections()}
                onAnalyze={() => void analyzeBook()}
                onRetryChapter={(id) => void retryChapter(id)}
                onUpdateType={(bookType) => void updateBookType(bookType)}
                onSaveSections={(sections) => void saveSections(sections)}
                onUpdateModel={(modelId) => void updateBookModel(modelId)}
                models={settings?.available_models || []}
                onCreateProject={() => {
                  setView("projects");
                  setSelectedBook(null);
                }}
                busy={Boolean(busy)}
                activeRun={selectedBookRun}
                onCancelRun={(id) => void cancelRun(id)}
              />
            ) : (
              <LibraryView books={books} onOpen={openBook} onUpload={uploadBook} />
            ))}

          {view === "projects" &&
            (selectedProject ? (
              <ProjectWorkspace
                key={`${selectedProject.id}:${selectedEpisode?.id || "none"}:${selectedEpisode?.versions?.find((item) => item.stage === "final")?.version || 0}`}
                project={selectedProject}
                episode={selectedEpisode}
                batch={batch}
                onBack={() => { setSelectedProject(null); setSelectedEpisode(null); }}
                onConfirm={() => void confirmOutline()}
                onSaveOutline={(episodes) => void saveOutline(episodes)}
                onOpenEpisode={openEpisode}
                onGenerate={generateEpisode}
                onGenerateAll={() => void generateAll()}
                onExportAll={() => void exportProjectFinals()}
                onGenerateOutline={(requirements, count, wordMin, wordMax) =>
                  void generateProjectOutline(
                    requirements,
                    count,
                    wordMin,
                    wordMax,
                  )
                }
                onUpdateModel={(stage, modelId) =>
                  void updateProjectModel(stage, modelId)
                }
                models={settings?.available_models || []}
                onSaveFinal={saveFinalVersion}
                busy={Boolean(busy)}
                activeRun={selectedProjectRun}
                episodeRun={selectedEpisodeRun}
                onCancelRun={(id) => void cancelRun(id)}
                onRetryModule={(runId, moduleKey) =>
                  void retryAlbumModule(runId, moduleKey)
                }
              />
            ) : (
              <ProjectsView
                projects={projects}
                books={books}
                onOpen={openProject}
                onCreate={createProject}
              />
            ))}

          {view === "runs" && (
            <RunsView
              books={books}
              projects={projects}
              runs={runs}
              busy={busy}
              onCancel={(id) => void cancelRun(id)}
              onRetryModule={(runId, moduleKey) =>
                void retryAlbumModule(runId, moduleKey)
              }
            />
          )}

          {view === "prompts" && (
            <PromptSettingsView
              projects={projects}
              onDirtyChange={setPromptDirty}
            />
          )}

          {view === "settings" && (
            <SettingsView
              key={settings?.current_model_id || settings?.model || "loading"}
              status={settings}
              vaultPath={vaultPath}
              setVaultPath={setVaultPath}
              selectedBook={selectedBook}
              selectedProject={selectedProject}
              books={books}
              projects={projects}
              onSelectBook={(id) => {
                const book = books.find((item) => item.id === id) || null;
                setSelectedBook(book);
              }}
              onSelectProject={(id) => {
                const project = projects.find((item) => item.id === id) || null;
                setSelectedProject(project);
              }}
              onSync={() => void syncObsidian()}
              onSelectModel={selectGlobalModel}
              busy={Boolean(busy)}
            />
          )}
        </div>
      </section>
    </main>
  );
}

function LibraryView({
  books,
  onOpen,
  onUpload,
}: {
  books: Book[];
  onOpen: (id: string) => Promise<void>;
  onUpload: (event: FormEvent<HTMLFormElement>) => Promise<void>;
}) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const fileSize = selectedFile
    ? selectedFile.size >= 1024 * 1024
      ? `${(selectedFile.size / 1024 / 1024).toFixed(1)} MB`
      : `${Math.max(1, Math.round(selectedFile.size / 1024))} KB`
    : "";
  return (
    <>
      <section className="hero-grid">
        <div className="hero-copy">
          <span className="section-kicker">从原书开始，证据始终可追溯</span>
          <h2>把一本书变成可复用的知识资产。</h2>
          <p>
            先确认章节，再自动提取观点、论据、案例与金句。拆书结果独立保存，
            以后可被多个内容项目重复使用。
          </p>
        </div>
        <form className="upload-card" onSubmit={onUpload}>
          <label className={selectedFile ? "file-drop selected" : "file-drop"}>
            <input
              ref={fileInput}
              name="file"
              type="file"
              accept=".epub,.txt,.md,.markdown"
              onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
            />
            <span className="upload-glyph">{selectedFile ? "✓" : "＋"}</span>
            <strong>
              {selectedFile?.name || "选择 EPUB、TXT 或 Markdown"}
            </strong>
            <small>
              {selectedFile
                ? `${selectedFile.name.split(".").pop()?.toUpperCase()} · ${fileSize} · 已选择`
                : "原书只保存在本机，不进入 Git"}
            </small>
          </label>
          {selectedFile && (
            <div className="file-actions">
              <button type="button" onClick={() => fileInput.current?.click()}>
                重新选择
              </button>
              <button
                type="button"
                onClick={() => {
                  if (fileInput.current) fileInput.current.value = "";
                  setSelectedFile(null);
                }}
              >
                移除
              </button>
            </div>
          )}
          <div className="form-row">
            <input name="title" placeholder="书名（可自动识别）" />
            <input name="author" placeholder="作者" />
          </div>
          <label className="book-type-field">
            <span>书籍类型</span>
            <select name="book_type" defaultValue="non_narrative">
              <option value="non_narrative">非叙事类 · 观点、知识与案例</option>
              <option value="narrative">叙事类 · 人物关系与剧情</option>
            </select>
          </label>
          <button className="primary-button" type="submit" disabled={!selectedFile}>
            导入并解析
          </button>
        </form>
      </section>

      <div className="section-heading">
        <div>
          <p className="eyebrow">BOOKS</p>
          <h2>已入库书籍</h2>
        </div>
        <span>{books.length} 本书</span>
      </div>

      <div className="card-grid">
        {books.map((book) => (
          <button className="book-card" key={book.id} onClick={() => void onOpen(book.id)}>
            <div className="book-cover">
              <span>{book.title.slice(0, 1)}</span>
              <small>{book.source_type.toUpperCase()}</small>
            </div>
            <div className="book-card-body">
              <div className="card-topline">
                <span className={`state-pill state-${book.status}`}>
                  {statusLabels[book.status] || book.status}
                </span>
                <small>{book.author || "作者未填写"}</small>
              </div>
              <h3>{book.title}</h3>
              <p>{book.book_type === "narrative" ? "叙事类" : "非叙事类"} · {book.filename}</p>
              <div className="metric-row">
                <span><strong>{formatCount(book.article_count)}</strong> 篇文章</span>
                <span><strong>{formatCount(book.knowledge_count)}</strong> 条知识</span>
              </div>
            </div>
          </button>
        ))}
        {books.length === 0 && (
          <div className="empty-state">
            <span>书</span>
            <h3>还没有书籍</h3>
            <p>从上方导入《圆圈正义》或其他 EPUB、TXT、Markdown 文件。</p>
          </div>
        )}
      </div>
    </>
  );
}

function BookWorkspace({
  book,
  onBack,
  onConfirm,
  onAnalyze,
  onRetryChapter,
  onUpdateType,
  onUpdateModel,
  onSaveSections,
  onCreateProject,
  models,
  busy,
  activeRun,
  onCancelRun,
}: {
  book: Book;
  onBack: () => void;
  onConfirm: () => void;
  onAnalyze: () => void;
  onRetryChapter: (sectionId: string) => void;
  onUpdateType: (bookType: BookType) => void;
  onUpdateModel: (modelId: string | null) => void;
  onSaveSections: (sections: Section[]) => void;
  onCreateProject: () => void;
  models: ModelOption[];
  busy: boolean;
  activeRun: WorkflowRun | null;
  onCancelRun: (id: string) => void;
}) {
  const structuralSections = (book.sections || []).filter(
    (section) => section.level <= 4,
  );
  const rootSections = structuralSections.filter((section) => !section.parent_id);
  const latestChapterByRoot = useMemo(() => {
    const map = new Map<string, ChapterAnalysis>();
    for (const analysis of book.chapter_analyses || []) {
      if (!map.has(analysis.root_section_id)) {
        map.set(analysis.root_section_id, analysis);
      }
    }
    return map;
  }, [book.chapter_analyses]);
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    for (const item of book.knowledge || []) result[item.kind] = (result[item.kind] || 0) + 1;
    return result;
  }, [book.knowledge]);
  const [sourcePreview, setSourcePreview] = useState<EvidenceFragment | null>(null);

  const openSource = async (contentIndex: string) => {
    const fragment = await request<EvidenceFragment>(
      `/api/source-fragments/${contentIndex}`,
    );
    setSourcePreview(fragment);
  };

  return (
    <>
      <button className="back-button" onClick={onBack}>← 返回书籍库</button>
      <div className="book-hero">
        <div className="book-monogram">{book.title.slice(0, 1)}</div>
        <div className="book-hero-main">
          <span className={`state-pill state-${book.status}`}>
            {statusLabels[book.status] || book.status}
          </span>
          <h2>{book.title}</h2>
          <p>{book.author || "作者未填写"} · {book.filename}</p>
          <label className="book-type-control">
            <span>书籍类型</span>
            <select
              value={book.book_type}
              disabled={busy}
              onChange={(event) => {
                const nextType = event.target.value as BookType;
                if (
                  window.confirm(
                    "修改书籍类型后需要重新拆书，已有知识资产会保留到重新拆书完成。是否继续？",
                  )
                ) {
                  onUpdateType(nextType);
                } else {
                  event.currentTarget.value = book.book_type;
                }
              }}
            >
              <option value="non_narrative">非叙事类</option>
              <option value="narrative">叙事类</option>
            </select>
            <small>修改类型后需要重新拆书</small>
          </label>
          <label className="book-type-control">
            <span>章节拆书模型</span>
            <select
              value={book.analysis_model_id || ""}
              disabled={busy}
              onChange={(event) =>
                onUpdateModel(event.target.value || null)
              }
            >
              <option value="">
                跟随全局 · {book.effective_analysis_model?.label || "当前模型"}
              </option>
              {models.map((model) => (
                <option value={model.id} key={model.id}>{model.label}</option>
              ))}
            </select>
            <small>
              实际使用 {book.effective_analysis_model?.label || "全局模型"}
            </small>
          </label>
        </div>
        <div className="action-stack">
          {book.status === "segment_review" && (
            <button className="primary-button" disabled={busy} onClick={onConfirm}>
              确认章节切分
            </button>
          )}
          {["ready_to_analyze", "analysis_partial", "analysis_partial_failed"].includes(book.status) && (
            <button className="primary-button" disabled={busy || Boolean(activeRun)} onClick={onAnalyze}>
              {["analysis_partial", "analysis_partial_failed"].includes(book.status)
                ? activeRun ? "拆书任务执行中…" : "继续或重试章节拆书"
                : activeRun ? "拆书任务执行中…" : "开始拆书与知识入库"}
            </button>
          )}
          {book.status === "analyzed" && (
            <button className="primary-button" disabled={busy} onClick={onCreateProject}>
              用这本书创建内容项目
            </button>
          )}
        </div>
      </div>

      {activeRun && (
        <TaskProgressCard run={activeRun} onCancel={onCancelRun} />
      )}

      <div className="stage-strip">
        {[
          ["01", "章节切分", book.status !== "segment_review"],
          ["02", "知识拆解", book.status === "analyzed"],
          ["03", "思维导图", Boolean(book.mind_map)],
        ].map(([number, label, complete]) => (
          <div className={complete ? "stage complete" : "stage"} key={String(number)}>
            <span>{String(number)}</span>
            <strong>{String(label)}</strong>
            <small>{complete ? "已完成" : "等待处理"}</small>
          </div>
        ))}
      </div>

      <div className="book-columns">
        <section className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">STRUCTURE</p><h3>目录与文章</h3></div>
            <span>{rootSections.filter((item) => item.analysis_enabled).length} 章纳入拆书</span>
          </div>
          {book.status === "segment_review" ? (
            <ChapterEditor
              sections={book.sections || []}
              onSave={onSaveSections}
              disabled={busy}
            />
          ) : (
            <div className="section-list">
              {structuralSections.map((section) => (
                <div className={`section-row level-${section.level}`} key={section.id}>
                  <span>H{section.level}</span>
                  <div>
                    <strong>{section.title}</strong>
                    {section.content && <small>{section.content.slice(0, 72)}…</small>}
                    {!section.parent_id && !section.analysis_enabled && (
                      <small>{section.analysis_exclusion_reason || "已人工排除"}</small>
                    )}
                  </div>
                  <em>
                    {!section.parent_id
                      ? section.analysis_enabled
                        ? latestChapterByRoot.has(section.id)
                          ? latestChapterByRoot.get(section.id)?.status === "partial"
                            ? "部分成功"
                            : "拆书成功"
                          : "等待拆书"
                        : "不纳入"
                      : section.status === "confirmed"
                        ? "已确认"
                        : "待确认"}
                  </em>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">KNOWLEDGE</p><h3>知识资产</h3></div>
            <span>{book.knowledge?.length || 0} 条</span>
          </div>
          <div className="knowledge-metrics">
            {[
              "概念",
              "观点",
              "论据",
              "案例",
              "金句",
              ...(book.book_type === "narrative" ? ["人物关系"] : []),
            ].map((kind) => (
              <div key={kind}><strong>{counts[kind] || 0}</strong><span>{kind}</span></div>
            ))}
          </div>
          {book.chapter_analyses?.length ? (
            <div className="chapter-analysis-list">
              {rootSections
                .filter((section) => section.analysis_enabled)
                .map((section) => {
                  const analysis = latestChapterByRoot.get(section.id);
                  const precise =
                    Boolean(analysis?.fragment_set_id) &&
                    analysis?.fragment_set_id === book.fragment_set?.id;
                  const complete = precise && analysis?.status === "succeeded";
                  return (
                    <details key={section.id} className="chapter-analysis-card">
                      <summary>
                        <div>
                          <strong>{section.title}</strong>
                          <small>
                            {analysis
                              ? precise
                                ? analysis.status === "partial"
                                  ? `v${analysis.version} · 部分成功 · ${analysis.valid_item_count} 条有效 / ${analysis.invalid_item_count} 条未通过`
                                  : `v${analysis.version} · ${analysis.model} · 段落级溯源`
                                : `v${analysis.version} · 历史结果，需重跑升级溯源`
                              : "尚未成功"}
                          </small>
                        </div>
                        {!complete && (
                          <button
                            type="button"
                            disabled={busy || Boolean(activeRun)}
                            onClick={(event) => {
                              event.preventDefault();
                              onRetryChapter(section.id);
                            }}
                          >
                            单章重跑
                          </button>
                        )}
                      </summary>
                      <pre>{analysis?.rendered_markdown || "该章尚无成功拆书稿。"}</pre>
                      {analysis?.validation_issues_json?.length ? (
                        <div className="validation-issues">
                          <strong>未通过校验的条目</strong>
                          {analysis.validation_issues_json.map((issue, index) => (
                            <div key={`${issue.asset_type}-${index}`}>
                              <span>{issue.asset_type}</span>
                              <p>{issue.title}</p>
                              <small>
                                {issue.error}
                                {issue.source_content_indexes?.length
                                  ? ` · ${issue.source_content_indexes.join("、")}`
                                  : ""}
                              </small>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </details>
                  );
                })}
            </div>
          ) : null}
          <div className="knowledge-list">
            {(book.knowledge || []).slice(0, 12).map((item) => (
              <article key={item.id}>
                <span className={`kind kind-${item.kind}`}>{item.kind}</span>
                <h4>{item.title}</h4>
                <p>{item.body}</p>
                {item.source_content_indexes?.length ? (
                  <div className="source-indexes">
                    {item.source_content_indexes.map((contentIndex) => (
                      <button
                        type="button"
                        key={contentIndex}
                        onClick={() => void openSource(contentIndex)}
                      >
                        {contentIndex}
                      </button>
                    ))}
                  </div>
                ) : (
                  <small>历史资产：暂无段落级原文索引</small>
                )}
              </article>
            ))}
            {!book.knowledge?.length && (
              <div className="panel-empty">确认章节后即可自动拆书并生成知识资产。</div>
            )}
          </div>
          {sourcePreview && (
            <aside className="source-preview">
              <button type="button" onClick={() => setSourcePreview(null)}>关闭</button>
              <small>{sourcePreview.content_index}</small>
              <strong>{sourcePreview.section_path_json?.join(" / ")}</strong>
              <p>{sourcePreview.text}</p>
            </aside>
          )}
        </section>

        <section className="panel map-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">MAP</p><h3>全书思维导图</h3></div>
          </div>
          <pre>{book.mind_map?.content || "拆书完成后将在这里生成全书知识结构。"}</pre>
        </section>
      </div>
    </>
  );
}

function ChapterEditor({
  sections,
  onSave,
  disabled,
}: {
  sections: Section[];
  onSave: (sections: Section[]) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(sections);
  const structural = draft.filter((section) => section.level <= 4);

  const update = (id: string, patch: Partial<Section>) =>
    setDraft((current) =>
      current.map((section) => (section.id === id ? { ...section, ...patch } : section)),
    );

  const move = (id: string, direction: -1 | 1) => {
    const current = structural.findIndex((section) => section.id === id);
    const target = current + direction;
    if (current < 0 || target < 0 || target >= structural.length) return;
    const a = structural[current];
    const b = structural[target];
    setDraft((items) =>
      items
        .map((item) => {
          if (item.id === a.id) return { ...item, position: b.position };
          if (item.id === b.id) return { ...item, position: a.position };
          return item;
        })
        .sort((left, right) => left.position - right.position),
    );
  };

  const mergePrevious = (id: string) => {
    const index = structural.findIndex((section) => section.id === id);
    if (index <= 0) return;
    const current = structural[index];
    const previous = structural[index - 1];
    if (previous.level !== current.level) return;
    setDraft((items) =>
      items
        .filter((item) => item.id !== current.id)
        .map((item) => {
          if (item.id === previous.id) {
            return {
              ...item,
              content: `${item.content}\n\n${current.title}\n${current.content}`.trim(),
            };
          }
          if (item.parent_id === current.id) return { ...item, parent_id: previous.id };
          return item;
        }),
    );
  };

  const splitSection = (id: string) => {
    const section = draft.find((item) => item.id === id);
    if (!section || section.level !== 4 || section.content.length < 200) return;
    const paragraphs = section.content.split(/\n+/).filter(Boolean);
    if (paragraphs.length < 2) return;
    const half = Math.ceil(paragraphs.length / 2);
    const first = paragraphs.slice(0, half).join("\n\n");
    const second = paragraphs.slice(half).join("\n\n");
    const newId = crypto.randomUUID().replaceAll("-", "");
    setDraft((items) =>
      [
        ...items.map((item) =>
          item.id === id ? { ...item, content: first } : item,
        ),
        {
          ...section,
          id: newId,
          title: `${section.title}（续）`,
          content: second,
          position: section.position + 0.5,
          status: "draft",
        },
      ]
        .sort((left, right) => left.position - right.position)
        .map((item, index) => ({ ...item, position: index })),
    );
  };

  return (
    <div className="chapter-editor">
      <div className="editor-help">
        审核完整一至三级目录。一级章节可选择是否纳入拆书，也可调整标题和层级。
      </div>
      {structural.map((section, index) => (
        <div className={`chapter-edit-row level-${section.level}`} key={section.id}>
          <div className="chapter-order">
            <button disabled={index === 0} onClick={() => move(section.id, -1)}>↑</button>
            <button disabled={index === structural.length - 1} onClick={() => move(section.id, 1)}>↓</button>
          </div>
          <select
            value={section.level}
            onChange={(event) => update(section.id, { level: Number(event.target.value) })}
          >
            <option value={1}>一级</option>
            <option value={2}>二级</option>
            <option value={3}>三级</option>
            <option value={4}>四级</option>
          </select>
          <input
            value={section.title}
            onChange={(event) => update(section.id, { title: event.target.value })}
          />
          <div className="chapter-tools">
            {!section.parent_id && (
              <label className="analysis-toggle">
                <input
                  type="checkbox"
                  checked={section.analysis_enabled}
                  onChange={(event) =>
                    update(section.id, {
                      analysis_enabled: event.target.checked,
                      analysis_exclusion_reason: event.target.checked
                        ? ""
                        : "已人工排除",
                    })
                  }
                />
                纳入逐章拆书
              </label>
            )}
            <button onClick={() => mergePrevious(section.id)}>并入上条</button>
            {section.level === 4 && <button onClick={() => splitSection(section.id)}>拆分</button>}
          </div>
        </div>
      ))}
      <button
        className="save-outline-button"
        disabled={disabled}
        onClick={() =>
          onSave(
            [...draft]
              .sort((left, right) => left.position - right.position)
              .map((item, index) => ({ ...item, position: index })),
          )
        }
      >
        保存章节调整
      </button>
    </div>
  );
}

function ProjectsView({
  projects,
  books,
  onOpen,
  onCreate,
}: {
  projects: Project[];
  books: Book[];
  onOpen: (id: string) => Promise<void>;
  onCreate: (event: FormEvent<HTMLFormElement>) => Promise<void>;
}) {
  const availableBooks = books.filter((book) => book.status === "analyzed");
  return (
    <>
      <section className="project-intro">
        <div>
          <span className="section-kicker">内容项目与原书拆解相互独立</span>
          <h2>从知识资产出发，组织一套声音专辑。</h2>
          <p>首版一个项目选择一本书；底层来源关系已支持以后融合多本书。</p>
        </div>
        <form className="create-project-card" onSubmit={onCreate}>
          <input name="title" placeholder="项目名称，例如：圆圈正义精读专辑" />
          <select name="book_id" defaultValue="">
            <option value="" disabled>选择已完成拆解的书籍</option>
            {availableBooks.map((book) => (
              <option value={book.id} key={book.id}>{book.title}</option>
            ))}
          </select>
          <button className="primary-button" type="submit">创建内容项目</button>
        </form>
      </section>

      <div className="section-heading">
        <div><p className="eyebrow">PROJECTS</p><h2>创作中的项目</h2></div>
        <span>{projects.length} 个项目</span>
      </div>
      <div className="project-grid">
        {projects.map((project) => {
          const total = project.episode_count || 0;
          const complete = project.completed_count || 0;
          const progress = total ? Math.round((complete / total) * 100) : 0;
          return (
            <button className="project-card" key={project.id} onClick={() => void onOpen(project.id)}>
              <div className="card-topline">
                <span className={`state-pill state-${project.status}`}>
                  {statusLabels[project.status] || project.status}
                </span>
                <small>{complete} / {total} 条声音</small>
              </div>
              <h3>{project.title}</h3>
              <p>来源书籍 {project.book_ids.length} 本</p>
              <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
              <div className="project-card-foot"><span>{progress}%</span><strong>进入工作台 →</strong></div>
            </button>
          );
        })}
        {!projects.length && (
          <div className="empty-state"><span>声</span><h3>还没有内容项目</h3><p>先完成一本书的知识拆解，再生成专辑大纲。</p></div>
        )}
      </div>
    </>
  );
}

function ProjectWorkspace({
  project,
  episode,
  batch,
  onBack,
  onConfirm,
  onSaveOutline,
  onOpenEpisode,
  onGenerate,
  onGenerateAll,
  onExportAll,
  onGenerateOutline,
  onUpdateModel,
  models,
  onSaveFinal,
  busy,
  activeRun,
  episodeRun,
  onCancelRun,
  onRetryModule,
}: {
  project: Project;
  episode: Episode | null;
  batch: BatchRun | null;
  onBack: () => void;
  onConfirm: () => void;
  onSaveOutline: (episodes: Episode[]) => void;
  onOpenEpisode: (id: string) => Promise<void>;
  onGenerate: (stage: "outline" | "draft" | "final") => void;
  onGenerateAll: () => void;
  onExportAll: () => void;
  onGenerateOutline: (
    specialRequirements: string,
    desiredEpisodeCount: number | null,
    episodeWordCountMin: number,
    episodeWordCountMax: number,
  ) => void;
  onUpdateModel: (
    stage: ProjectModelStage,
    modelId: string | null,
  ) => void;
  models: ModelOption[];
  onSaveFinal: (content: string) => Promise<boolean>;
  busy: boolean;
  activeRun: WorkflowRun | null;
  episodeRun: WorkflowRun | null;
  onCancelRun: (id: string) => void;
  onRetryModule: (runId: string, moduleKey: string) => void;
}) {
  const latestByStage = useMemo(() => {
    const map: Partial<Record<"outline" | "draft" | "final", ArtifactVersion>> = {};
    for (const version of episode?.versions || []) {
      if (!map[version.stage] || version.version > map[version.stage]!.version) map[version.stage] = version;
    }
    return map;
  }, [episode]);
  const finalVersions = useMemo(
    () =>
      (episode?.versions || [])
        .filter((version) => version.stage === "final")
        .sort((left, right) => right.version - left.version),
    [episode],
  );
  const batchByEpisode = useMemo(
    () =>
      new Map(
        (batch?.children || []).map((child) => [child.scope_id, child]),
      ),
    [batch],
  );
  const [finalDraft, setFinalDraft] = useState(
    latestByStage.final?.content || "",
  );
  const [dirty, setDirty] = useState(false);
  const [outlineDirty, setOutlineDirty] = useState(false);
  const [specialRequirements, setSpecialRequirements] = useState(
    project.album_special_requirements || "",
  );
  const [desiredEpisodeCount, setDesiredEpisodeCount] = useState(
    project.desired_episode_count?.toString() || "",
  );
  const [episodeWordCountMin, setEpisodeWordCountMin] = useState(
    (project.episode_word_count_min || 2000).toString(),
  );
  const [episodeWordCountMax, setEpisodeWordCountMax] = useState(
    (project.episode_word_count_max || 2500).toString(),
  );
  const [generationValidationError, setGenerationValidationError] =
    useState("");
  const batchActive = Boolean(
    batch && ["pending", "running"].includes(batch.status),
  );
  const batchDone =
    (batch?.summary.completed || 0) + (batch?.summary.failed || 0);
  const batchPercent = batch?.summary.total
    ? Math.round((batchDone / batch.summary.total) * 100)
    : 0;
  const allFinalsReady = Boolean(
    project.episodes?.length &&
    project.episodes.every((item) =>
      ["completed", "review", "approved"].includes(item.status),
    ),
  );
  const activeChild = episode ? batchByEpisode.get(episode.id) : undefined;
  const retryStage =
    activeChild?.status === "failed" &&
    ["match_episode_sources", "outline", "draft", "final"].includes(
      activeChild.error_stage || "",
    )
      ? (
          activeChild.error_stage === "match_episode_sources"
            ? "outline"
            : activeChild.error_stage
        ) as "outline" | "draft" | "final"
      : null;

  const confirmLeave = () =>
    !dirty || window.confirm("当前终稿还有未保存修改，确定要放弃并离开吗？");

  const selectEpisode = (episodeId: string) => {
    if (confirmLeave()) void onOpenEpisode(episodeId);
  };

  const saveDraft = async () => {
    const saved = await onSaveFinal(finalDraft);
    if (saved) setDirty(false);
  };
  const exportAllFinals = () => {
    if (
      dirty &&
      !window.confirm(
        "当前终稿还有未保存修改，导出文件将使用最近一次已保存版本。是否继续？",
      )
    ) {
      return;
    }
    onExportAll();
  };
  const finalWordCount = useMemo(
    () => countSpokenWords(finalDraft),
    [finalDraft],
  );
  const wordCountMinimum = project.episode_word_count_min || 2000;
  const wordCountMaximum = project.episode_word_count_max || 2500;
  const finalWordCountWarning = !finalDraft.trim()
    ? ""
    : finalWordCount < wordCountMinimum
      ? `字数提醒：当前 ${finalWordCount} 字，低于预期 ${wordCountMinimum}–${wordCountMaximum} 字，还差约 ${wordCountMinimum - finalWordCount} 字。文稿仍可正常保存和审核。`
      : finalWordCount > wordCountMaximum
        ? `字数提醒：当前 ${finalWordCount} 字，高于预期 ${wordCountMinimum}–${wordCountMaximum} 字，超出约 ${finalWordCount - wordCountMaximum} 字。文稿仍可正常保存和审核。`
        : "";

  const startAlbumGeneration = () => {
    const minimum = Number(episodeWordCountMin);
    const maximum = Number(episodeWordCountMax);
    if (
      !Number.isInteger(minimum) ||
      !Number.isInteger(maximum) ||
      minimum < 300 ||
      maximum > 10000 ||
      minimum > maximum
    ) {
      setGenerationValidationError(
        "每集字数需填写 300–10000 之间的整数，且最少字数不能大于最多字数。",
      );
      return;
    }
    setGenerationValidationError("");
    onGenerateOutline(
      specialRequirements,
      desiredEpisodeCount ? Number(desiredEpisodeCount) : null,
      minimum,
      maximum,
    );
  };

  return (
    <>
      <button
        className="back-button"
        onClick={() => {
          if (confirmLeave()) onBack();
        }}
      >
        ← 返回内容项目
      </button>
      <div className="project-head">
        <div>
          <span className={`state-pill state-${project.status}`}>
            {statusLabels[project.status] || project.status}
          </span>
          <h2>{project.title}</h2>
          <p>来源书籍 {project.book_ids.length} 本 · {project.episodes?.length || 0} 条声音</p>
        </div>
        {project.status === "outline_review" && (
          <button
            className="primary-button"
            disabled={busy || outlineDirty || Boolean(activeRun)}
            onClick={onConfirm}
          >
            {outlineDirty ? "请先保存大纲修改" : "确认专辑大纲并进入生产"}
          </button>
        )}
      </div>

      {activeRun && (
        <TaskProgressCard
          run={activeRun}
          onCancel={onCancelRun}
          onRetryModule={onRetryModule}
        />
      )}

      <details className="project-model-config">
        <summary>
          <div>
            <p className="eyebrow">项目模型</p>
            <strong>按生产环节选择模型</strong>
          </div>
          <span>未覆盖的环节跟随全局模型</span>
        </summary>
        <div className="project-model-grid">
          {projectModelStages.map((stage) => {
            const effective = project.effective_models?.[stage];
            return (
              <label key={stage}>
                <span>{projectModelStageLabels[stage]}</span>
                <select
                  value={project.model_overrides?.[stage] || ""}
                  disabled={busy}
                  onChange={(event) =>
                    onUpdateModel(stage, event.target.value || null)
                  }
                >
                  <option value="">
                    跟随全局 · {project.global_model?.label || "当前模型"}
                  </option>
                  {models.map((model) => (
                    <option value={model.id} key={model.id}>
                      {model.label}
                    </option>
                  ))}
                </select>
                <small>
                  实际使用 {effective?.label || "等待读取"}
                </small>
              </label>
            );
          })}
        </div>
      </details>

      {project.status === "outline_review" && (
        <section className="album-generation-card">
          <div>
            <p className="eyebrow">模型编排</p>
            <h3>生成思维导图与专辑大纲</h3>
            <p>系统先用轻量章节目录规划全书，再分模块生成 Markdown 大纲；每集字数会继续传给细纲、初稿和终稿，终稿超出预期范围时仅作提醒。</p>
            <div className="model-summary">
              <span>
                思维导图 · {project.effective_models?.mind_map.label || "—"}
              </span>
              <span>
                专辑大纲 · {project.effective_models?.album_outline.label || "—"}
              </span>
            </div>
          </div>
          <textarea
            value={specialRequirements}
            onChange={(event) => setSpecialRequirements(event.target.value)}
            placeholder="专辑特殊要求（可选），例如受众、侧重点、内容顺序或不希望涉及的内容"
          />
          <div className="album-generation-actions">
            <label>
              目标集数（允许上下浮动 2 集）
              <input
                type="number"
                min={1}
                max={500}
                value={desiredEpisodeCount}
                onChange={(event) => setDesiredEpisodeCount(event.target.value)}
                placeholder="例如 15，将生成 13–17 集"
              />
            </label>
            <div className="word-count-fields">
              <label>
                每集最少字数
                <input
                  type="number"
                  min={300}
                  max={10000}
                  value={episodeWordCountMin}
                  onChange={(event) =>
                    setEpisodeWordCountMin(event.target.value)
                  }
                />
              </label>
              <label>
                每集最多字数
                <input
                  type="number"
                  min={300}
                  max={10000}
                  value={episodeWordCountMax}
                  onChange={(event) =>
                    setEpisodeWordCountMax(event.target.value)
                  }
                />
              </label>
            </div>
            <button
              className="primary-button"
              disabled={busy || Boolean(activeRun)}
              onClick={startAlbumGeneration}
            >
              生成思维导图与专辑大纲
            </button>
          </div>
          {generationValidationError && (
            <div className="outline-validation">
              {generationValidationError}
            </div>
          )}
          {project.episode_count_notice && (
            <div className="episode-count-notice">{project.episode_count_notice}</div>
          )}
        </section>
      )}

      <div className={project.status === "outline_review" ? "studio-grid outline-mode" : "studio-grid"}>
        <section className="episode-rail">
          <div className="panel-heading">
            <div><p className="eyebrow">专辑</p><h3>声音目录</h3></div>
            <span>{project.episodes?.length || 0} 条</span>
          </div>
          {project.status === "outline_review" ? (
            <OutlineEditor
              episodes={project.episodes || []}
              onSave={onSaveOutline}
              onDirtyChange={setOutlineDirty}
              disabled={busy}
            />
          ) : (
            <>
              <div className="batch-card">
                <div className="batch-card-title">
                  <div>
                    <strong>
                      {batchActive ? "正在批量生产" : batch ? "最近一次生产" : "等待开始生产"}
                    </strong>
                    <span>声音之间最多 5 条并行</span>
                    <span className="batch-model-summary">
                      细纲 {project.effective_models?.episode_outline.label || "—"}
                      {" · "}初稿 {project.effective_models?.episode_draft.label || "—"}
                      {" · "}终稿 {project.effective_models?.episode_final.label || "—"}
                    </span>
                  </div>
                  <em>{batch ? `${batchPercent}%` : "0%"}</em>
                </div>
                <div className="batch-track">
                  <span style={{ width: `${batchPercent}%` }} />
                </div>
                <div className="batch-metrics">
                  <span>完成 <strong>{batch?.summary.completed || 0}</strong></span>
                  <span>进行 <strong>{batch?.summary.running || 0}</strong></span>
                  <span>失败 <strong>{batch?.summary.failed || 0}</strong></span>
                </div>
                <div className="batch-actions">
                  <button
                    className="primary-button batch-button"
                    disabled={busy || batchActive || allFinalsReady}
                    onClick={onGenerateAll}
                  >
                    {batchActive
                      ? "正在生成全部终稿…"
                      : allFinalsReady
                        ? "全部终稿已生成"
                        : "生成全部终稿"}
                  </button>
                  <button
                    className="quiet-button batch-button"
                    disabled={busy}
                    onClick={exportAllFinals}
                  >
                    导出全部终稿
                  </button>
                </div>
              </div>
              <div className="episode-list">
                {(project.episodes || []).map((item) => {
                  const child = batchByEpisode.get(item.id);
                  const label = child?.status === "failed"
                    ? `失败 · ${stageLabels[child.error_stage as keyof typeof stageLabels] || "生成"}`
                    : statusLabels[item.status] || item.status;
                  return (
                    <button
                      key={item.id}
                      className={episode?.id === item.id ? "episode-row active" : "episode-row"}
                      onClick={() => selectEpisode(item.id)}
                    >
                      <span>{String(item.position).padStart(2, "0")}</span>
                      <div>
                        <strong>{item.title}</strong>
                        <small>{item.content_type} · {item.style}</small>
                      </div>
                      <em className={child?.status === "failed" ? "failed" : ""}>{label}</em>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </section>

        <section className="editor-panel">
          {!episode ? (
            <div className="editor-empty">
              <span>稿</span><h3>选择一条声音终稿</h3><p>终稿将在这里放大显示，细纲和初稿默认折叠。</p>
            </div>
          ) : (
            <>
              {episodeRun && (
                <TaskProgressCard
                  run={episodeRun}
                  compact
                  onCancel={onCancelRun}
                />
              )}
              <div className="editor-head">
                <div>
                  <p className="eyebrow">声音 {String(episode.position).padStart(2, "0")}</p>
                  <h3>{episode.title}</h3>
                  <span>
                    {episode.content_type} · {episode.style} · 引用{" "}
                    {episode.knowledge_item_ids?.length || 0} 条知识资产
                  </span>
                  <p className="episode-framework">{episode.content_framework}</p>
                </div>
                <div className="editor-actions">
                  {retryStage && (
                    <button
                      className="primary-button"
                      disabled={busy || Boolean(episodeRun)}
                      onClick={() => onGenerate(retryStage)}
                    >
                      从失败的{stageLabels[retryStage]}重跑
                    </button>
                  )}
                  <button className="quiet-button" disabled={busy || Boolean(episodeRun)} onClick={() => onGenerate("outline")}>
                    重新生成整条
                  </button>
                </div>
              </div>

              <div className="supporting-artifacts">
                {(["outline", "draft"] as const).map((stage) => {
                  const artifact = latestByStage[stage];
                  return (
                    <details className="supporting-card" key={stage}>
                      <summary>
                        <div>
                          <strong>{stageLabels[stage]}</strong>
                          <span>{artifact ? `v${artifact.version} · ${artifact.model}` : "未生成"}</span>
                        </div>
                        <span>展开查看</span>
                      </summary>
                      <div className="supporting-body">
                        {artifact ? artifact.content : "等待上一步完成"}
                      </div>
                      {artifact && (
                        <footer>
                          <span>prompt {artifact.prompt_version}</span>
                          <button disabled={busy || Boolean(episodeRun)} onClick={() => onGenerate(stage)}>
                            从这里重跑
                          </button>
                        </footer>
                      )}
                    </details>
                  );
                })}
              </div>

              <div className="final-editor-block">
                <div className="final-editor-heading">
                  <div>
                    <p className="eyebrow">主要审核内容</p>
                    <h3>声音终稿</h3>
                  </div>
                  <span>
                    {latestByStage.final
                      ? `${latestByStage.final.author_type === "human" ? "人工编辑" : "模型生成"} · v${latestByStage.final.version}`
                      : "等待生成"}
                  </span>
                </div>
                <textarea
                  className="final-editor"
                  aria-label="声音终稿编辑器"
                  value={finalDraft}
                  placeholder="批量生产完成后，声音终稿会显示在这里。"
                  onChange={(event) => {
                    setFinalDraft(event.target.value);
                    setDirty(true);
                  }}
                />
                {finalWordCountWarning && (
                  <p className="word-count-warning" role="status">
                    {finalWordCountWarning}
                  </p>
                )}
                <div className="final-editor-footer">
                  <span className={dirty ? "unsaved" : ""}>
                    {dirty ? "有未保存修改" : "已保存"}
                  </span>
                  <div>
                    <button
                      className="quiet-button"
                      disabled={busy || !latestByStage.draft}
                      onClick={() => onGenerate("final")}
                    >
                      重新口语化
                    </button>
                    <button
                      className="primary-button"
                      disabled={busy || !dirty || !finalDraft.trim()}
                      onClick={() => void saveDraft()}
                    >
                      保存修改 · 新建版本
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}
        </section>

        {project.status !== "outline_review" && (
          <aside className="review-inspector">
            {!episode ? (
              <div className="inspector-empty">选择声音后查看证据与版本。</div>
            ) : (
              <>
                <div className="panel-heading">
                  <div><p className="eyebrow">证据</p><h3>原文引用</h3></div>
                  <span>
                    {episode.evidence?.direct_fragments.length
                      || episode.sources?.length
                      || 0} 条
                  </span>
                </div>
                {episode.evidence?.knowledge_items.length ? (
                  <div className="episode-assets">
                    {episode.evidence.knowledge_items.map((item) => (
                      <article key={item.id}>
                        <span>{item.kind}</span>
                        <strong>{item.title}</strong>
                        <p>{item.body}</p>
                      </article>
                    ))}
                  </div>
                ) : null}
                <div className="evidence-list">
                  {(episode.evidence?.direct_fragments || []).map((source) => (
                    <details key={source.content_index}>
                      <summary>
                        <span>{source.section_path_json.join(" / ")}</span>
                        <small>{source.content_index}</small>
                      </summary>
                      <p>{source.text}</p>
                    </details>
                  ))}
                  {!episode.evidence?.direct_fragments.length &&
                    (episode.sources || []).map((source) => (
                      <details key={source.id}>
                        <summary>
                          <span>{source.title}</span>
                          <small>{source.id.slice(0, 8)}</small>
                        </summary>
                        <p>{source.content}</p>
                      </details>
                    ))}
                </div>
                {episode.evidence?.auxiliary_fragments.length ? (
                  <details className="auxiliary-evidence">
                    <summary>
                      相邻辅助上下文 · {episode.evidence.auxiliary_fragments.length} 条
                    </summary>
                    {episode.evidence.auxiliary_fragments.map((source) => (
                      <p key={source.content_index}>
                        <small>{source.content_index}</small>
                        {source.text}
                      </p>
                    ))}
                  </details>
                ) : null}
                <div className="version-panel">
                  <div className="panel-heading">
                    <div><p className="eyebrow">历史</p><h3>终稿版本</h3></div>
                    <span>{finalVersions.length} 个</span>
                  </div>
                  <div className="version-list">
                    {finalVersions.map((version) => (
                      <button
                        key={version.id}
                        className={version.id === latestByStage.final?.id ? "version-row active" : "version-row"}
                        onClick={() => {
                          setFinalDraft(version.content);
                          setDirty(version.id !== latestByStage.final?.id);
                        }}
                      >
                        <div>
                          <strong>v{version.version}</strong>
                          <span>{version.author_type === "human" ? "人工编辑" : version.model}</span>
                        </div>
                        <small>
                          {version.id === latestByStage.final?.id ? "当前" : "作为编辑起点"}
                        </small>
                      </button>
                    ))}
                    {!finalVersions.length && (
                      <div className="panel-empty">终稿生成后会保留版本记录。</div>
                    )}
                  </div>
                </div>
              </>
            )}
          </aside>
        )}
      </div>
    </>
  );
}

function OutlineEditor({
  episodes,
  onSave,
  onDirtyChange,
  disabled,
}: {
  episodes: Episode[];
  onSave: (episodes: Episode[]) => void;
  onDirtyChange: (dirty: boolean) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(episodes);
  const [validationError, setValidationError] = useState("");
  const update = (id: string, patch: Partial<Episode>) => {
    onDirtyChange(true);
    setDraft((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  };
  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= draft.length) return;
    const next = [...draft];
    [next[index], next[target]] = [next[target], next[index]];
    onDirtyChange(true);
    setDraft(next.map((item, itemIndex) => ({ ...item, position: itemIndex + 1 })));
  };
  const remove = (id: string) => {
    onDirtyChange(true);
    setDraft((current) =>
      current
        .filter((entry) => entry.id !== id)
        .map((entry, index) => ({ ...entry, position: index + 1 })),
    );
  };
  const save = () => {
    const invalid = draft.find(
      (item) =>
        !item.title.trim() ||
        !item.content_framework.trim() ||
        !item.section_identifier.trim() ||
        !item.source_section_ids.length,
    );
    if (invalid) {
      setValidationError(
        `第 ${invalid.position} 条声音需要填写标题、主要内容并关联来源章节。`,
      );
      return;
    }
    setValidationError("");
    onSave(draft);
    onDirtyChange(false);
  };
  return (
    <div className="outline-editor">
      <div className="editor-help">
        确认前请审核标题、类型、声音内容框架和来源章节。生产细纲前，系统会在这些章节内匹配具体知识资产与原文块。
      </div>
      {validationError && <div className="outline-validation">{validationError}</div>}
      {draft.map((item, index) => (
        <div className="outline-edit-card" key={item.id}>
          <div className="outline-edit-top">
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <button disabled={index === 0} onClick={() => move(index, -1)}>↑</button>
              <button disabled={index === draft.length - 1} onClick={() => move(index, 1)}>↓</button>
              <button onClick={() => remove(item.id)}>删除</button>
            </div>
          </div>
          <input value={item.title} onChange={(event) => update(item.id, { title: event.target.value })} />
          <textarea
            className="framework-editor"
            aria-label={`第 ${index + 1} 条声音内容框架`}
            value={item.content_framework}
            placeholder="填写本集的主要内容、事件范围和讲述顺序"
            onChange={(event) =>
              update(item.id, { content_framework: event.target.value })
            }
          />
          <label className="outline-identifier">
            <span>来源章节</span>
            <div className="outline-chapter-tags">
              {(item.section_identifier || "")
                .split("、")
                .filter(Boolean)
                .map((chapter) => <code key={chapter}>{chapter}</code>)}
            </div>
          </label>
          {(item.source_content_indexes || []).length > 0 && (
            <div className="outline-source-indexes">
              <span>原文索引</span>
              <div>
                {item.source_content_indexes.map((sourceIndex) => (
                  <code key={sourceIndex}>{sourceIndex}</code>
                ))}
              </div>
            </div>
          )}
          <div className="form-row">
            <select value={item.content_type} onChange={(event) => update(item.id, { content_type: event.target.value })}>
              <option>解读</option><option>过渡</option><option>故事</option>
            </select>
            <select value={item.style} onChange={(event) => update(item.id, { style: event.target.value })}>
              <option>观点</option><option>鸡汤</option>
            </select>
          </div>
        </div>
      ))}
      <button className="save-outline-button" disabled={disabled || !draft.length} onClick={save}>
        保存大纲调整
      </button>
    </div>
  );
}

function TaskProgressCard({
  run,
  onCancel,
  onRetryModule,
  compact = false,
}: {
  run: WorkflowRun;
  onCancel: (id: string) => void;
  onRetryModule?: (runId: string, moduleKey: string) => void;
  compact?: boolean;
}) {
  const [outputs, setOutputs] = useState<RunOutput[]>([]);
  const [outputError, setOutputError] = useState("");
  const [clock, setClock] = useState(0);
  const progress = run.progress_total
    ? Math.round((run.progress_current / run.progress_total) * 100)
    : run.status === "succeeded"
      ? 100
      : 0;
  const started = run.started_at || run.created_at;
  const end = run.finished_at
    ? new Date(run.finished_at).getTime()
    : clock || new Date(run.updated_at).getTime();
  const elapsedSeconds = Math.max(
    0,
    Math.round((end - new Date(started).getTime()) / 1000),
  );
  const stageEntries = Object.entries(run.metadata_json?.stages || {});

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let active = true;
    request<{ outputs: RunOutput[] }>(`/api/runs/${run.id}/outputs`)
      .then((result) => {
        if (!active) return;
        setOutputs(result.outputs);
        setOutputError("");
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setOutputError(
          caught instanceof Error ? caught.message : "阶段结果读取失败",
        );
      });
    return () => {
      active = false;
    };
  }, [run.id, run.updated_at]);

  return (
    <section className={compact ? "task-progress-card compact" : "task-progress-card"}>
      <header>
        <div>
          <p className="eyebrow">后台持久任务</p>
          <h3>{run.scope_label || "内容生成任务"}</h3>
          <span>
            当前：{workflowStageLabels[run.current_stage] || run.current_stage || "等待开始"}
            {" · "}已运行 {elapsedSeconds} 秒
          </span>
        </div>
        <strong>{progress}%</strong>
      </header>
      <div className="task-progress-track">
        <span style={{ width: `${progress}%` }} />
      </div>
      <div className="task-progress-meta">
        <span>{run.message || "任务已经进入后台"}</span>
        <span>
          {run.progress_current} / {run.progress_total || "—"}
        </span>
      </div>
      {stageEntries.length > 0 && (
        <div className="task-stage-list">
          {stageEntries.map(([stage, detail]) => (
            <span className={`task-stage ${detail.status}`} key={stage}>
              <i />
              {workflowStageLabels[stage] || stage}
              <em>{detail.status}</em>
            </span>
          ))}
        </div>
      )}
      {outputs.length > 0 && (
        <div className="task-outputs">
          {outputs.map((output, index) => (
            <details key={`${output.stage}-${output.id || index}`}>
              <summary>
                <strong>{output.label}</strong>
                <span>
                  {output.status === "failed"
                    ? "生成失败"
                    : output.version
                      ? `v${output.version}`
                      : "已完成"}
                  {output.model ? ` · ${output.model}` : ""}
                </span>
              </summary>
              <pre>{output.content || output.error_message || "等待重跑"}</pre>
              {output.status === "failed" &&
                output.module_key &&
                onRetryModule && (
                  <footer>
                    <button
                      className="quiet-button"
                      onClick={() => onRetryModule(run.id, output.module_key!)}
                    >
                      重跑此模块
                    </button>
                  </footer>
                )}
            </details>
          ))}
        </div>
      )}
      {outputError && <p className="task-output-error">{outputError}</p>}
      {["pending", "running"].includes(run.status) && (
        <footer>
          <button className="text-button danger" onClick={() => onCancel(run.id)}>
            取消任务
          </button>
        </footer>
      )}
    </section>
  );
}

function RunsView({
  books,
  projects,
  runs,
  busy,
  onCancel,
  onRetryModule,
}: {
  books: Book[];
  projects: Project[];
  runs: WorkflowRun[];
  busy: string;
  onCancel: (id: string) => void;
  onRetryModule: (runId: string, moduleKey: string) => void;
}) {
  const runLabel = (run: WorkflowRun, index: number) => {
    if (run.scope_type === "chapter_analysis") {
      return `章节任务 · ${String(index + 1).padStart(2, "0")}`;
    }
    if (run.scope_type === "book_analysis_batch") return "全书拆书任务";
    if (run.scope_type === "project_generation") return "专辑规划任务";
    if (run.scope_type === "project_batch") return "整张专辑生产";
    return `声音任务 · ${String(index + 1).padStart(2, "0")}`;
  };
  const runModels = (run: WorkflowRun) => {
    const stageModels = run.metadata_json?.stage_model_ids;
    if (stageModels) {
      return (["outline", "draft", "final"] as const)
        .filter((stage) => stageModels[stage])
        .map((stage) => `${stageLabels[stage]} ${stageModels[stage]}`)
        .join(" · ");
    }
    return run.metadata_json?.model || run.metadata_json?.model_id || "";
  };
  const activeTopRuns = runs.filter(
    (run) =>
      !run.parent_run_id && ["pending", "running"].includes(run.status),
  );
  return (
    <div className="runs-layout">
      <section className="runs-summary">
        <span className="section-kicker">持久运行，不因关闭网页丢失状态</span>
        <h2>
          {busy ||
            (activeTopRuns.length
              ? `${activeTopRuns.length} 个任务正在后台执行`
              : "当前没有正在执行的任务")}
        </h2>
        <p>刷新网页后会自动找回任务；每个模型阶段完成后即可查看完整结果。</p>
        <div className="run-stats">
          <div><strong>{books.length}</strong><span>书籍</span></div>
          <div><strong>{projects.length}</strong><span>项目</span></div>
          <div><strong>{runs.filter((item) => item.status === "succeeded").length}</strong><span>成功运行</span></div>
        </div>
        <div className="runs-active-list">
          {activeTopRuns.map((run) => (
            <TaskProgressCard
              key={run.id}
              run={run}
              compact
              onCancel={onCancel}
              onRetryModule={onRetryModule}
            />
          ))}
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">RECENT</p><h3>最近状态</h3></div></div>
        <div className="timeline">
          {runs.map((run, index) => (
            <div key={run.id}>
              <span className={run.status === "succeeded" ? "timeline-dot done" : "timeline-dot"} />
              <small>{runLabel(run, index)}</small>
              <strong>
                {run.scope_label || "未命名任务"} ·{" "}
                {workflowStageLabels[run.current_stage] ||
                  stageLabels[run.stage as keyof typeof stageLabels] ||
                  run.stage}
              </strong>
              <p>{run.status} {run.message && `· ${run.message}`}</p>
              {run.progress_total > 0 && (
                <p>
                  进度 {run.progress_current} / {run.progress_total}
                </p>
              )}
              {runModels(run) && <p className="run-models">{runModels(run)}</p>}
              {["pending", "running"].includes(run.status) && (
                <button className="cancel-run" onClick={() => onCancel(run.id)}>取消</button>
              )}
            </div>
          ))}
          {!runs.length && <div className="panel-empty">生成声音后，运行状态会显示在这里。</div>}
        </div>
      </section>
    </div>
  );
}

const promptStages: PromptStage[] = [
  "mind_map",
  "album_module_plan",
  "album_outline",
  "episode_outline",
  "episode_draft",
  "episode_final",
];

const promptStageFallbackLabels: Record<PromptStage, string> = {
  mind_map: "思维导图",
  album_module_plan: "全书知识模块设计",
  album_outline: "分模块专辑大纲",
  episode_outline: "声音细纲",
  episode_draft: "声音初稿",
  episode_final: "声音终稿",
};

const promptStageInputNotes: Record<PromptStage, string> = {
  mind_map: "输入：完整或压缩后的全书拆书稿。",
  album_module_plan: "输入：策划版全书拆书稿、轻量章节目录和专辑要求。",
  album_outline: "输入：当前知识模块任务和该模块对应的详细拆书稿。",
  episode_outline: "输入：当前声音框架和所属模块拆书稿；不读取段落级原文。",
  episode_draft: "输入：最新声音细纲和当前声音匹配到的段落级原文。",
  episode_final: "输入：最新声音初稿和当前声音匹配到的段落级原文。",
};

const episodePromptStages = new Set<PromptStage>([
  "episode_outline",
  "episode_draft",
  "episode_final",
]);

function promptDraftIssues(
  config: PromptTemplateConfig | null,
  draft: string,
): string[] {
  if (!config) return [];
  const issues: string[] = [];
  if (!draft.trim()) issues.push("提示词不能为空");
  if (draft.length > 50_000) issues.push("提示词不能超过 50,000 个字符");
  const tokenPattern = /\{\{([a-z][a-z0-9_]*)\}\}/g;
  const withoutTokens = draft.replace(tokenPattern, "");
  if (withoutTokens.includes("{{") || withoutTokens.includes("}}")) {
    issues.push("存在不完整的占位符花括号");
  }
  const used = new Set(Array.from(draft.matchAll(tokenPattern), (match) => match[1]));
  const unknown = Array.from(used).filter(
    (name) => !(name in config.allowed_placeholders),
  );
  if (unknown.length) issues.push(`未知占位符：${unknown.join("、")}`);
  const missing = config.required_placeholders.filter((name) => !used.has(name));
  if (missing.length) issues.push(`缺少必要占位符：${missing.join("、")}`);
  for (const group of config.required_placeholder_groups || []) {
    if (!group.some((name) => used.has(name))) {
      issues.push(`至少保留一个材料占位符：${group.join(" / ")}`);
    }
  }
  return issues;
}

function PromptSettingsView({
  projects,
  onDirtyChange,
}: {
  projects: Project[];
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [scope, setScope] = useState<"global" | "project">("global");
  const [projectId, setProjectId] = useState(projects[0]?.id || "");
  const [templates, setTemplates] = useState<PromptTemplateConfig[]>([]);
  const [selectedStage, setSelectedStage] =
    useState<PromptStage>("mind_map");
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<PromptHistoryVersion[]>([]);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [episodeId, setEpisodeId] = useState("");
  const [modules, setModules] = useState<PromptModuleOption[]>([]);
  const [moduleKey, setModuleKey] = useState("");
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dirty, setDirty] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  const current = useMemo(
    () => templates.find((item) => item.stage_key === selectedStage) || null,
    [selectedStage, templates],
  );
  const issues = useMemo(
    () => promptDraftIssues(current, draft),
    [current, draft],
  );

  const load = useCallback(async () => {
    if (scope === "project" && !projectId) {
      setTemplates([]);
      setHistory([]);
      setEpisodes([]);
      setModules([]);
      return;
    }
    const projectQuery =
      scope === "project" ? `?project_id=${encodeURIComponent(projectId)}` : "";
    const historyParams = new URLSearchParams({
      stage_key: selectedStage,
      scope,
    });
    if (scope === "project") historyParams.set("project_id", projectId);
    const requests: [
      Promise<PromptTemplateConfig[]>,
      Promise<PromptHistoryVersion[]>,
      Promise<Project | null>,
      Promise<PromptModuleOption[]>,
    ] = [
      request<PromptTemplateConfig[]>(`/api/prompts/templates${projectQuery}`),
      request<PromptHistoryVersion[]>(
        `/api/prompts/history?${historyParams.toString()}`,
      ),
      scope === "project"
        ? request<Project>(`/api/projects/${projectId}`)
        : Promise.resolve(null),
      scope === "project"
        ? request<PromptModuleOption[]>(
            `/api/projects/${projectId}/prompt-modules`,
          )
        : Promise.resolve([]),
    ];
    const [nextTemplates, nextHistory, project, nextModules] =
      await Promise.all(requests);
    const nextCurrent =
      nextTemplates.find((item) => item.stage_key === selectedStage) || null;
    setTemplates(nextTemplates);
    setHistory(nextHistory);
    setDraft(nextCurrent?.user_template || "");
    setEpisodes(project?.episodes || []);
    setModules(nextModules);
    setEpisodeId((currentEpisodeId) => {
      if (project?.episodes?.some((item) => item.id === currentEpisodeId)) {
        return currentEpisodeId;
      }
      return project?.episodes?.[0]?.id || "";
    });
    setModuleKey((currentModuleKey) => {
      if (
        nextModules.some((item) => item.module_key === currentModuleKey)
      ) {
        return currentModuleKey;
      }
      return nextModules[0]?.module_key || "";
    });
    setPreview(null);
    setDirty(false);
    onDirtyChange(false);
  }, [onDirtyChange, projectId, scope, selectedStage]);

  useEffect(() => {
    let active = true;
    Promise.resolve()
      .then(load)
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "载入提示词失败");
        }
      })
      .finally(() => {
        if (active) setBusy("");
      });
    return () => {
      active = false;
    };
  }, [load]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const changeDraft = (value: string) => {
    setDraft(value);
    setDirty(value !== current?.user_template);
    onDirtyChange(value !== current?.user_template);
    setPreview(null);
  };

  const confirmDiscard = () =>
    !dirty || window.confirm("提示词还有未保存修改，确定放弃吗？");

  const changeScope = (nextScope: "global" | "project") => {
    if (scope === nextScope || !confirmDiscard()) return;
    setScope(nextScope);
    setPreview(null);
  };

  const changeStage = (stage: PromptStage) => {
    if (selectedStage === stage || !confirmDiscard()) return;
    setSelectedStage(stage);
    setPreview(null);
  };

  const runPromptAction = async (
    label: string,
    action: () => Promise<void>,
  ) => {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(`${label}完成`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `${label}失败`);
    } finally {
      setBusy("");
    }
  };

  const save = () =>
    runPromptAction("保存新版本", async () => {
      if (issues.length) throw new Error(issues[0]);
      await request("/api/prompts/versions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stage_key: selectedStage,
          scope,
          project_id: scope === "project" ? projectId : null,
          user_template: draft,
        }),
      });
      await load();
    });

  const restore = (version: PromptHistoryVersion) => {
    if (!window.confirm(`恢复 ${scope === "global" ? "全局" : "项目"} v${version.version}？恢复会创建一个新版本。`)) {
      return;
    }
    void runPromptAction("恢复历史版本", async () => {
      await request("/api/prompts/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stage_key: selectedStage,
          scope,
          project_id: scope === "project" ? projectId : null,
          version_id: version.id,
        }),
      });
      await load();
    });
  };

  const reset = () => {
    const message =
      scope === "global"
        ? "恢复系统默认？现有全局历史版本会保留。"
        : "取消当前项目覆盖？该项目将重新跟随全局提示词。";
    if (!window.confirm(message)) return;
    void runPromptAction(
      scope === "global" ? "恢复系统默认" : "取消项目覆盖",
      async () => {
        const path =
          scope === "global"
            ? `/api/prompts/global/${selectedStage}`
            : `/api/projects/${projectId}/prompts/${selectedStage}`;
        await request(path, { method: "DELETE" });
        await load();
      },
    );
  };

  const showPreview = () =>
    runPromptAction("生成预览", async () => {
      if (issues.length) throw new Error(issues[0]);
      setPreview(
        await request<PromptPreview>("/api/prompts/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            stage_key: selectedStage,
            project_id: scope === "project" ? projectId : null,
            episode_id:
              scope === "project" && episodePromptStages.has(selectedStage)
                ? episodeId || null
                : null,
            module_key:
              scope === "project" && selectedStage === "album_outline"
                ? moduleKey || null
                : null,
            user_template: draft,
          }),
        }),
      );
    });

  const insertPlaceholder = (name: string) => {
    const editor = editorRef.current;
    const token = `{{${name}}}`;
    const start = editor?.selectionStart ?? draft.length;
    const end = editor?.selectionEnd ?? draft.length;
    changeDraft(`${draft.slice(0, start)}${token}${draft.slice(end)}`);
    window.requestAnimationFrame(() => {
      editor?.focus();
      editor?.setSelectionRange(start + token.length, start + token.length);
    });
  };

  return (
    <div className="prompt-settings-page">
      <section className="prompt-toolbar">
        <div className="prompt-scope-switch" aria-label="提示词作用范围">
          <button
            className={scope === "global" ? "active" : ""}
            onClick={() => changeScope("global")}
          >
            全局默认
          </button>
          <button
            className={scope === "project" ? "active" : ""}
            onClick={() => changeScope("project")}
          >
            项目覆盖
          </button>
        </div>
        {scope === "project" && (
          <label className="prompt-project-select">
            <span>内容项目</span>
            <select
              value={projectId}
              onChange={(event) => {
                if (!confirmDiscard()) return;
                setProjectId(event.target.value);
              }}
            >
              <option value="">选择内容项目</option>
              {projects.map((project) => (
                <option value={project.id} key={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <p>
          {scope === "global"
            ? "未设置项目覆盖的内容项目会自动使用这里的最新版本。"
            : "项目未保存覆盖时继续跟随全局；保存后仅影响当前项目。"}
        </p>
      </section>

      {(notice || error) && (
        <div className={error ? "prompt-message error" : "prompt-message"}>
          {error || notice}
        </div>
      )}

      {scope === "project" && !projectId ? (
        <div className="empty-card">
          <span>词</span>
          <h2>请选择内容项目</h2>
          <p>选择项目后，可以为六个生产环节设置独立提示词。</p>
        </div>
      ) : (
        <div className="prompt-workbench">
          <aside className="prompt-stage-rail">
            <p className="eyebrow">WORKFLOW</p>
            <h2>提示词环节</h2>
            {promptStages.map((stage) => {
              const item = templates.find((template) => template.stage_key === stage);
              return (
                <button
                  key={stage}
                  className={selectedStage === stage ? "active" : ""}
                  onClick={() => changeStage(stage)}
                >
                  <strong>{item?.label || promptStageFallbackLabels[stage]}</strong>
                  <span>{item?.source_label || "载入中"}</span>
                </button>
              );
            })}
          </aside>

          <section className="prompt-editor-panel">
            <header>
              <div>
                <p className="eyebrow">USER PROMPT TEMPLATE</p>
                <h2>{current?.label || "提示词模板"}</h2>
                <span>
                  当前生效：{current?.source_label || "—"} · 系统约束{" "}
                  {current?.system_version || "—"}
                </span>
              </div>
              {dirty && <em>未保存</em>}
            </header>
            <div className="editor-help">
              <strong>{promptStageInputNotes[selectedStage]}</strong>
              <span>
                你只需要配置主要创作指令和占位符；输出结构、来源边界、
                结构校验与字数目标由系统保护。
              </span>
            </div>
            <textarea
              ref={editorRef}
              value={draft}
              onChange={(event) => changeDraft(event.target.value)}
              spellCheck={false}
              aria-label="用户提示词模板"
            />
            {issues.length > 0 && (
              <div className="prompt-validation">
                {issues.map((issue) => <span key={issue}>{issue}</span>)}
              </div>
            )}
            {scope === "project" &&
              selectedStage === "album_outline" &&
              modules.length > 0 && (
                <label className="prompt-preview-episode">
                  <span>预览使用的模块</span>
                  <select
                    value={moduleKey}
                    onChange={(event) => {
                      setModuleKey(event.target.value);
                      setPreview(null);
                    }}
                  >
                    {modules.map((module) => (
                      <option key={module.module_key} value={module.module_key}>
                        {String(module.position).padStart(2, "0")} · {module.title}
                        {" · "}
                        {module.character_count.toLocaleString("zh-CN")} 字符
                      </option>
                    ))}
                  </select>
                </label>
              )}
            {scope === "project" &&
              episodePromptStages.has(selectedStage) &&
              episodes.length > 0 && (
                <label className="prompt-preview-episode">
                  <span>预览使用的声音</span>
                  <select
                    value={episodeId}
                    onChange={(event) => {
                      setEpisodeId(event.target.value);
                      setPreview(null);
                    }}
                  >
                    {episodes.map((episode) => (
                      <option key={episode.id} value={episode.id}>
                        {String(episode.position).padStart(2, "0")} · {episode.title}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            <footer>
              <button
                className="quiet-button"
                disabled={Boolean(busy) || issues.length > 0}
                onClick={() => void showPreview()}
              >
                预览填充结果
              </button>
              <button
                className="primary-button"
                disabled={Boolean(busy) || !dirty || issues.length > 0}
                onClick={() => void save()}
              >
                {busy || "保存为新版本"}
              </button>
            </footer>
            {preview && (
              <div className="prompt-preview">
                <section className="prompt-input-materials">
                  <div>
                    <strong>本次真实输入材料</strong>
                    <span>{preview.input_materials.length} 项</span>
                  </div>
                  {preview.input_materials.map((material) => (
                    <details key={material.key}>
                      <summary>
                        <div>
                          <strong>{material.label}</strong>
                          <small>{material.source}</small>
                        </div>
                        <span>
                          {material.character_count.toLocaleString("zh-CN")} 字符
                          {material.compressed ? " · 已压缩" : ""}
                        </span>
                      </summary>
                      <pre>{material.content || "当前项目尚无可用内容。"}</pre>
                    </details>
                  ))}
                </section>
                <div>
                  <strong>用户模板渲染结果</strong>
                  {preview.truncated && <span>包含长内容，可滚动查看</span>}
                </div>
                <pre>{preview.rendered_user_template}</pre>
                <details>
                  <summary>查看系统追加的受保护约束</summary>
                  <pre>{preview.protected_suffix}</pre>
                </details>
              </div>
            )}
          </section>

          <aside className="prompt-meta-panel">
            <section>
              <p className="eyebrow">PLACEHOLDERS</p>
              <h3>可用占位符</h3>
              <div className="placeholder-list">
                {Object.entries(current?.allowed_placeholders || {}).map(
                  ([name, description]) => (
                    <button key={name} onClick={() => insertPlaceholder(name)}>
                      <code>{`{{${name}}}`}</code>
                      <span>{description}</span>
                      <em>
                        {current?.required_placeholders.includes(name)
                          ? "必要"
                          : current?.required_placeholder_groups.some(
                                (group) => group.includes(name),
                              )
                            ? "必选一"
                            : "可选"}
                      </em>
                    </button>
                  ),
                )}
              </div>
            </section>
            <section className="prompt-history">
              <div className="prompt-history-heading">
                <div><p className="eyebrow">VERSIONS</p><h3>历史版本</h3></div>
                {(scope === "global"
                  ? current?.has_global_override
                  : current?.has_project_override) && (
                  <button className="text-button danger" onClick={reset}>
                    {scope === "global" ? "恢复系统默认" : "取消项目覆盖"}
                  </button>
                )}
              </div>
              {history.length ? history.map((version) => (
                <details key={version.id}>
                  <summary>
                    <div>
                      <strong>v{version.version}</strong>
                      <span>{new Date(version.created_at).toLocaleString("zh-CN")}</span>
                    </div>
                    <span>查看</span>
                  </summary>
                  <pre>{version.user_template}</pre>
                  <button
                    className="quiet-button"
                    disabled={Boolean(busy)}
                    onClick={() => restore(version)}
                  >
                    恢复为新版本
                  </button>
                </details>
              )) : <p className="prompt-empty-history">还没有用户保存的版本。</p>}
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}

function SettingsView({
  status,
  vaultPath,
  setVaultPath,
  selectedBook,
  selectedProject,
  books,
  projects,
  onSelectBook,
  onSelectProject,
  onSync,
  onSelectModel,
  busy,
}: {
  status: SettingsStatus | null;
  vaultPath: string;
  setVaultPath: (value: string) => void;
  selectedBook: Book | null;
  selectedProject: Project | null;
  books: Book[];
  projects: Project[];
  onSelectBook: (id: string) => void;
  onSelectProject: (id: string) => void;
  onSync: () => void;
  onSelectModel: (modelId: string) => Promise<boolean>;
  busy: boolean;
}) {
  const [modelId, setModelId] = useState(status?.current_model_id || "");
  const currentLabel =
    status?.available_models.find(
      (option) => option.id === status.current_model_id,
    )?.label || status?.model || "—";

  const saveModel = async () => {
    const saved = await onSelectModel(modelId);
    if (!saved) setModelId(status?.current_model_id || "");
  };

  return (
    <div className="settings-grid">
      <section className="settings-card model-settings-card">
        <p className="eyebrow">MODEL</p>
        <h2>全局模型</h2>
        <div className="setting-row"><span>当前供应商</span><strong>{status?.provider || "未连接"}</strong></div>
        <div className="setting-row"><span>当前模型</span><strong>{currentLabel}</strong></div>
        <label className="model-selector">
          <span>选择后续任务使用的模型</span>
          <select
            value={modelId}
            onChange={(event) => setModelId(event.target.value)}
          >
            {!status?.current_model_id && <option value="">选择模型</option>}
            {status?.available_models.map((option) => (
              <option value={option.id} key={option.id}>
                {option.label} · {option.model}
              </option>
            ))}
          </select>
        </label>
        <button
          className="primary-button model-save-button"
          disabled={
            busy ||
            !modelId ||
            modelId === status?.current_model_id
          }
          onClick={() => void saveModel()}
        >
          设为全局模型
        </button>
        <p className="setting-note model-scope-note">
          切换后立即生效：新任务使用新模型，正在运行的任务继续使用启动时的模型。
        </p>
        <div className="setting-row"><span>API Key</span><strong>{status?.api_key_configured ? "已通过环境变量配置" : "未配置"}</strong></div>
        <p className="setting-note">密钥只从本机环境变量读取，不写入数据库、日志或浏览器存储。</p>
      </section>
      <section className="settings-card obsidian-card">
        <p className="eyebrow">OBSIDIAN</p>
        <h2>同步到知识库</h2>
        <label>Vault 绝对路径<input value={vaultPath} onChange={(event) => setVaultPath(event.target.value)} placeholder="/Users/你的名字/Documents/My Vault" /></label>
        <div className="form-row">
          <select value={selectedBook?.id || ""} onChange={(event) => onSelectBook(event.target.value)}>
            <option value="">选择书籍（可选）</option>
            {books.map((book) => <option value={book.id} key={book.id}>{book.title}</option>)}
          </select>
          <select value={selectedProject?.id || ""} onChange={(event) => onSelectProject(event.target.value)}>
            <option value="">选择项目（可选）</option>
            {projects.map((project) => <option value={project.id} key={project.id}>{project.title}</option>)}
          </select>
        </div>
        <button className="primary-button" disabled={busy || !vaultPath || (!selectedBook && !selectedProject)} onClick={onSync}>增量同步</button>
        <p className="setting-note">重复同步不会生成重复笔记；个人批注保留区块不会被覆盖。</p>
      </section>
      <section className="settings-card full">
        <p className="eyebrow">LOCAL DATA</p>
        <h2>本地数据边界</h2>
        <div className="boundary-list">
          <div><span>01</span><strong>原书与数据库</strong><p>{status?.data_dir || "项目 data 目录"}</p></div>
          <div><span>02</span><strong>模型调用</strong><p>仅把当前节点所需原文发送给所选供应商</p></div>
          <div><span>03</span><strong>Obsidian</strong><p>只写入你明确选择的 Vault 路径</p></div>
        </div>
      </section>
    </div>
  );
}
