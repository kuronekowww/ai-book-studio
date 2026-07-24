"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";

type View = "library" | "projects" | "runs" | "settings";

type Book = {
  id: string;
  title: string;
  author: string;
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
};

type KnowledgeItem = {
  id: string;
  kind: string;
  title: string;
  body: string;
  source_section_ids: string[];
};

type Project = {
  id: string;
  title: string;
  status: string;
  book_ids: string[];
  episode_count?: number;
  completed_count?: number;
  episodes?: Episode[];
};

type Episode = {
  id: string;
  project_id: string;
  position: number;
  title: string;
  content_type: string;
  style: string;
  status: string;
  source_section_ids: string[];
  versions?: ArtifactVersion[];
  sources?: Section[];
};

type ArtifactVersion = {
  id: string;
  stage: "outline" | "draft" | "final";
  version: number;
  content: string;
  provider: string;
  model: string;
  prompt_version: string;
  author_type: "model" | "human";
  created_at: string;
};

type SettingsStatus = {
  provider: string;
  model: string;
  api_key_configured: boolean;
  data_dir: string;
};

type WorkflowRun = {
  id: string;
  scope_type: string;
  scope_id: string;
  stage: string;
  status: "pending" | "running" | "succeeded" | "partial_failed" | "failed" | "cancelled";
  message: string;
  parent_run_id?: string | null;
  error_stage?: string;
  position?: number;
  created_at: string;
  updated_at: string;
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
  failed: "生成失败",
  completed: "已完成",
};

const stageLabels = {
  outline: "声音细纲",
  draft: "声音初稿",
  final: "声音终稿",
};

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
      .then(([bookList, projectList, settingsStatus, runList]) => {
        if (!active) return;
        setBooks(bookList);
        setProjects(projectList);
        setSettings(settingsStatus);
        setRuns(runList);
        setBackendOnline(true);
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
    if (
      !selectedProject ||
      !batch ||
      !["pending", "running"].includes(batch.status)
    ) {
      return;
    }
    const projectId = selectedProject.id;
    const episodeId = selectedEpisode?.id;
    const interval = window.setInterval(() => {
      Promise.all([
        request<BatchRun | null>(`/api/projects/${projectId}/batch`),
        request<Project>(`/api/projects/${projectId}`),
        episodeId
          ? request<Episode>(`/api/episodes/${episodeId}`)
          : Promise.resolve(null),
      ])
        .then(([nextBatch, nextProject, nextEpisode]) => {
          setBatch(nextBatch);
          setSelectedProject(nextProject);
          if (nextEpisode) setSelectedEpisode(nextEpisode);
        })
        .catch((caught: unknown) => {
          setError(caught instanceof Error ? caught.message : "批次状态刷新失败");
        });
    }, 800);
    return () => window.clearInterval(interval);
  }, [batch, selectedEpisode?.id, selectedProject]);

  const runAction = async (label: string, action: () => Promise<void>) => {
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
    runAction("拆书与知识入库", async () => {
      await request(`/api/books/${selectedBook.id}/analyze`, { method: "POST" });
      setSelectedBook(await request<Book>(`/api/books/${selectedBook.id}`));
      await refresh();
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

  const generateAll = () =>
    selectedProject &&
    runAction("启动整张专辑生产", async () => {
      const result = await request<BatchRun>(
        `/api/projects/${selectedProject.id}/generate-all`,
        { method: "POST" },
      );
      setBatch(result);
      setSelectedProject(
        await request<Project>(`/api/projects/${selectedProject.id}`),
      );
      setNotice(`已启动 ${result.summary.total} 条声音，最多 5 条并行生产`);
      await refresh();
    });

  const generateEpisode = (fromStage: "outline" | "draft" | "final") =>
    selectedEpisode &&
    runAction(fromStage === "outline" ? "生成整条声音" : `从${stageLabels[fromStage]}重跑`, async () => {
      const run = await request<WorkflowRun>(
        `/api/episodes/${selectedEpisode.id}/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ from_stage: fromStage }),
        },
      );
      let current = run;
      for (let attempt = 0; attempt < 300; attempt += 1) {
        if (["succeeded", "failed", "cancelled"].includes(current.status)) break;
        await new Promise((resolve) => setTimeout(resolve, 400));
        current = await request<WorkflowRun>(`/api/runs/${run.id}`);
      }
      if (current.status !== "succeeded") {
        throw new Error(current.message || `生成任务${current.status}`);
      }
      setSelectedEpisode(
        await request<Episode>(`/api/episodes/${selectedEpisode.id}`),
      );
      if (selectedProject) {
        setSelectedProject(
          await request<Project>(`/api/projects/${selectedProject.id}`),
        );
      }
      await refresh();
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

  const navItems: { key: View; label: string; hint: string }[] = [
    { key: "library", label: "书籍知识库", hint: `${books.length} 本` },
    { key: "projects", label: "内容项目", hint: `${projects.length} 个` },
    { key: "runs", label: "运行记录", hint: busy ? "执行中" : "正常" },
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
              {view === "runs" && "WORKFLOW RUNS"}
              {view === "settings" && "LOCAL SETTINGS"}
            </p>
            <h1>
              {view === "library" && (selectedBook?.title || "书籍知识库")}
              {view === "projects" && (selectedProject?.title || "内容项目")}
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
                onSaveSections={(sections) => void saveSections(sections)}
                onCreateProject={() => {
                  setView("projects");
                  setSelectedBook(null);
                }}
                busy={Boolean(busy)}
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
                onSaveFinal={saveFinalVersion}
                busy={Boolean(busy)}
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
            />
          )}

          {view === "settings" && (
            <SettingsView
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
          <label className="file-drop">
            <input name="file" type="file" accept=".epub,.txt,.md,.markdown" />
            <span className="upload-glyph">＋</span>
            <strong>选择 EPUB、TXT 或 Markdown</strong>
            <small>原书只保存在本机，不进入 Git</small>
          </label>
          <div className="form-row">
            <input name="title" placeholder="书名（可自动识别）" />
            <input name="author" placeholder="作者" />
          </div>
          <button className="primary-button" type="submit">导入并解析</button>
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
              <p>{book.filename}</p>
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
  onSaveSections,
  onCreateProject,
  busy,
}: {
  book: Book;
  onBack: () => void;
  onConfirm: () => void;
  onAnalyze: () => void;
  onSaveSections: (sections: Section[]) => void;
  onCreateProject: () => void;
  busy: boolean;
}) {
  const structuralSections = (book.sections || []).filter((section) =>
    [3, 4].includes(section.level),
  );
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    for (const item of book.knowledge || []) result[item.kind] = (result[item.kind] || 0) + 1;
    return result;
  }, [book.knowledge]);

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
        </div>
        <div className="action-stack">
          {book.status === "segment_review" && (
            <button className="primary-button" disabled={busy} onClick={onConfirm}>
              确认章节切分
            </button>
          )}
          {book.status === "ready_to_analyze" && (
            <button className="primary-button" disabled={busy} onClick={onAnalyze}>
              开始拆书与知识入库
            </button>
          )}
          {book.status === "analyzed" && (
            <button className="primary-button" disabled={busy} onClick={onCreateProject}>
              用这本书创建内容项目
            </button>
          )}
        </div>
      </div>

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
            <span>{structuralSections.filter((item) => item.level === 4).length} 篇</span>
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
                  <span>{section.level === 3 ? "主题" : String(section.position).padStart(2, "0")}</span>
                  <div>
                    <strong>{section.title}</strong>
                    {section.level === 4 && <small>{section.content.slice(0, 72)}…</small>}
                  </div>
                  <em>{section.status === "confirmed" ? "已确认" : "待确认"}</em>
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
            {["观点", "案例", "金句"].map((kind) => (
              <div key={kind}><strong>{counts[kind] || 0}</strong><span>{kind}</span></div>
            ))}
          </div>
          <div className="knowledge-list">
            {(book.knowledge || []).slice(0, 12).map((item) => (
              <article key={item.id}>
                <span className={`kind kind-${item.kind}`}>{item.kind}</span>
                <h4>{item.title}</h4>
                <p>{item.body}</p>
                <small>来源：{item.source_section_ids.length} 个小节</small>
              </article>
            ))}
            {!book.knowledge?.length && (
              <div className="panel-empty">确认章节后即可自动拆书并生成知识资产。</div>
            )}
          </div>
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
  const structural = draft.filter((section) => [3, 4].includes(section.level));

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
      <div className="editor-help">可改标题和层级，也可调整顺序、合并或拆分长文章。</div>
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
            <option value={3}>主题</option>
            <option value={4}>文章</option>
          </select>
          <input
            value={section.title}
            onChange={(event) => update(section.id, { title: event.target.value })}
          />
          <div className="chapter-tools">
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
          <button className="primary-button" type="submit">生成专辑大纲</button>
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
  onSaveFinal,
  busy,
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
  onSaveFinal: (content: string) => Promise<boolean>;
  busy: boolean;
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
    ["outline", "draft", "final"].includes(activeChild.error_stage || "")
      ? activeChild.error_stage as "outline" | "draft" | "final"
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
          <button className="primary-button" disabled={busy} onClick={onConfirm}>
            确认专辑大纲并进入生产
          </button>
        )}
      </div>

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
              <div className="editor-head">
                <div>
                  <p className="eyebrow">声音 {String(episode.position).padStart(2, "0")}</p>
                  <h3>{episode.title}</h3>
                  <span>{episode.content_type} · {episode.style} · 引用 {episode.sources?.length || 0} 个原文小节</span>
                </div>
                <div className="editor-actions">
                  {retryStage && (
                    <button
                      className="primary-button"
                      disabled={busy}
                      onClick={() => onGenerate(retryStage)}
                    >
                      从失败的{stageLabels[retryStage]}重跑
                    </button>
                  )}
                  <button className="quiet-button" disabled={busy} onClick={() => onGenerate("outline")}>
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
                          <button disabled={busy} onClick={() => onGenerate(stage)}>
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
                <div className="final-editor-footer">
                  <span className={dirty ? "unsaved" : ""}>
                    {dirty ? "有未保存修改" : `${finalDraft.length} 字 · 已保存`}
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
                  <span>{episode.sources?.length || 0} 条</span>
                </div>
                <div className="evidence-list">
                  {(episode.sources || []).map((source) => (
                    <details key={source.id}>
                      <summary>
                        <span>{source.title}</span>
                        <small>{source.id.slice(0, 8)}</small>
                      </summary>
                      <p>{source.content}</p>
                    </details>
                  ))}
                </div>
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
  disabled,
}: {
  episodes: Episode[];
  onSave: (episodes: Episode[]) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(episodes);
  const update = (id: string, patch: Partial<Episode>) =>
    setDraft((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= draft.length) return;
    const next = [...draft];
    [next[index], next[target]] = [next[target], next[index]];
    setDraft(next.map((item, itemIndex) => ({ ...item, position: itemIndex + 1 })));
  };
  return (
    <div className="outline-editor">
      <div className="editor-help">确认前可改标题、类型、风格和顺序，也可删除声音。</div>
      {draft.map((item, index) => (
        <div className="outline-edit-card" key={item.id}>
          <div className="outline-edit-top">
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <button disabled={index === 0} onClick={() => move(index, -1)}>↑</button>
              <button disabled={index === draft.length - 1} onClick={() => move(index, 1)}>↓</button>
              <button onClick={() => setDraft((current) => current.filter((entry) => entry.id !== item.id))}>删除</button>
            </div>
          </div>
          <input value={item.title} onChange={(event) => update(item.id, { title: event.target.value })} />
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
      <button className="save-outline-button" disabled={disabled || !draft.length} onClick={() => onSave(draft)}>
        保存大纲调整
      </button>
    </div>
  );
}

function RunsView({
  books,
  projects,
  runs,
  busy,
  onCancel,
}: {
  books: Book[];
  projects: Project[];
  runs: WorkflowRun[];
  busy: string;
  onCancel: (id: string) => void;
}) {
  return (
    <div className="runs-layout">
      <section className="runs-summary">
        <span className="section-kicker">持久运行，不因关闭网页丢失状态</span>
        <h2>{busy || "当前没有正在执行的任务"}</h2>
        <p>每次生成都会记录输入快照、提示词版本、模型与产物版本。</p>
        <div className="run-stats">
          <div><strong>{books.length}</strong><span>书籍</span></div>
          <div><strong>{projects.length}</strong><span>项目</span></div>
          <div><strong>{runs.filter((item) => item.status === "succeeded").length}</strong><span>成功运行</span></div>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">RECENT</p><h3>最近状态</h3></div></div>
        <div className="timeline">
          {runs.map((run, index) => (
            <div key={run.id}>
              <span className={run.status === "succeeded" ? "timeline-dot done" : "timeline-dot"} />
              <small>声音任务 · {String(index + 1).padStart(2, "0")}</small>
              <strong>{stageLabels[run.stage as keyof typeof stageLabels] || run.stage}</strong>
              <p>{run.status} {run.message && `· ${run.message}`}</p>
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
  busy: boolean;
}) {
  return (
    <div className="settings-grid">
      <section className="settings-card">
        <p className="eyebrow">MODEL</p>
        <h2>模型调用</h2>
        <div className="setting-row"><span>当前供应商</span><strong>{status?.provider || "未连接"}</strong></div>
        <div className="setting-row"><span>模型</span><strong>{status?.model || "—"}</strong></div>
        <div className="setting-row"><span>API Key</span><strong>{status?.api_key_configured ? "已通过环境变量配置" : "演示模式无需密钥"}</strong></div>
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
