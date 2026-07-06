# Roadmap

最高のAHC自動化ソフトに向けたフェーズ計画。完了項目は実装コミットに紐づく。

前提となる設計方針:

- AtCoderの生成AIルール上、rated本番での「複数候補の自動生成+スコアによる自動選抜」は禁止。
  よって製品は2モード: **練習・研究ラボ**(完全自律OK、本リポジトリの主対象)と
  **コンテスト・コパイロット**(human-in-the-loop)。
- 参考にした先行研究: ALE-Agent/ALE-Bench(多様性優先探索+失敗からの洞察抽出)、
  ShinkaEvolve(適応的親サンプリング、novelty棄却、LLMバンディット)、
  AlphaEvolve/CodeEvolve(島進化、inspirations)、pahcer(並列ローカルテスト+Optuna)。

## Phase 0 — 採択ゲートの穴塞ぎ ✅ (`2edea22`)

- [x] 3値判定(accepted / neutral / rejected)+理由の記録
- [x] 候補がベースラインの解けるシードで失敗したら即棄却(クラッシュ候補の採択バグ修正)
- [x] 悪化シード数・悪化幅・中央値・経過時間キャップの予算制
- [x] スコア中立でも高速化していれば採択(neutral_speedup_ratio)
- [x] paired bootstrap による improve_confidence と min_improve_confidence ゲート
- [x] `config.yaml` の `acceptance:` ブロックで全パラメータ調整可

## Phase 1 — 評価基盤の高速化 ✅ (`2edea22`)

- [x] シード並列評価(デフォルト cpu-1 ワーカー、`--jobs`)
- [x] content-addressed ソーススナップショット(sha256)+ `source --run N [--checkout]`
- [x] (problem, source_hash, seed, params) キーの評価キャッシュ(`--no-cache`)
- [x] 段階カスケード評価(5→30→残り)と回復不能なゲート違反での早期打ち切り
- [x] 相対スコア(virtual best 比)の analyze/compare 表示
- [x] SQLite WAL + busy_timeout で並列書き込み耐性
- 実測: ahc067 10シードで 直列23.4s → 並列4.7s → キャッシュ0.85s

### Phase 1 持ち越し

- [ ] git worktree ベースのワークスペース(公式Rustツールの target/ バイナリ参照が課題)
- [ ] シード特徴抽出(入力パラメータ→弱点クラスタリング)
- [ ] 自作スコアラと公式ツールのクロスバリデーション

## Phase 2 — マルチ世代 autopilot ✅ (`5ab20ac`)

- [x] 世代ループ(`--generations N`): 分析→役割選択→並列トライアル→ベスト1採択→再ベースライン
- [x] トライアル並列実行(ワークスペース・アダプタ・DB接続をトライアルごとに分離、`--trial-jobs`)
- [x] 採択候補の自動マージ+ `patches` テーブル記録(`--no-merge` で記録のみ)
- [x] ホールドアウト検証シード(`--validation-seeds`)による過学習ゲート
- [x] 停滞検知(マージ0の世代で早期終了)、`--budget` 消化で終了
- [x] 孤児プロセス根絶(プロセスグループ SIGKILL)

## Phase 3 — 進化的探索と学習ループ 🔶 進行中

### 第1弾 ✅ (`96a9954`, `c2bd7f4`)

- [x] 系統樹: 全 run に `parent_run_id`(ベースライン→候補→マージ後の解の木をDBから復元可能)
- [x] 洞察の自動抽出: トライアル結果を `knowledge/<problem>_autopilot.md` に構造化追記、
      knowledge 検索経由で将来のプロンプトへ還流
- [x] インスピレーション注入: 直近マージ候補の unified diff をプロンプトに同梱
- [x] ツールタイムアウトの設定化(generator/score/adapter)— 実戦セッション15の学び
- [x] エージェントのワークスペース隔離ルール(root への書き込み事故対策)

### 第2弾 ✅ (`2eae8a1`)

- [x] **失敗フィードバック再試行**: 棄却理由・ゲート数値を同じワークスペースの
      エージェントに返して修正させるループ(セッション15の CutBuilder 誤診で価値実証済み)。
      `--repair-attempts N` で有効化。ビルド/アダプタ失敗もエラー文を返して再試行、
      修正でソースが変わらなければ早期打ち切り、試行履歴は summary の `repair.attempts`
      と insights に記録、各試行 run は `parent_run_id` で系統樹に連鎖。

### 第3弾 ✅ (`1a77eb8`)

- [x] **novelty フィルタ**: 既存候補と実質同一の diff を評価前に棄却。
      完全一致ハッシュ+コメント/空白を無視した正規化フィンガープリント
      (`snapshot_norms` にメモ化)の2段判定。重複は評価コストゼロで
      `duplicate`/`skipped` として記録し、修正ループ有効時は「非新規」の
      フィードバックを返して別案を促す。索引は世代ごとにスナップショットし、
      並列トライアル間の非決定的な重複判定を回避。`--no-novelty-filter` で無効化。
      あわせてカスケード早期打ち切り時の修正プロンプトに「未評価シードは
      打ち切りによるもの(クラッシュではない)」の注記を追加。

### 第4弾 ✅ (`4e8e1b2`)

- [x] **アーカイブ型進化探索**: 常に最新マインラインから出発ではなく、系統樹から
      fitness + novelty で親をサンプリングして分岐(ShinkaEvolve / island model 相当)。
      `--archive` で有効化。セッション内の全評価済み run(ソースハッシュで重複排除、
      全シード成功のみ)をアーカイブ化し、`(1 + fitness順位) / (1 + 子の数)` の重みで
      トライアルごとに親を抽選。親スナップショットをワークスペースに復元し、
      プロンプトには親 run の brief と系統注記を注入。採択ゲートは従来どおり
      マインライン基準なので、弱い親からの探索でもマージ品質は下がらない。
      選ばれた親は summary の `archive_parent` と run の `parent_run_id` に記録。

### 残り

- [ ] **動的役割生成**: 固定の specialist カタログではなく、オーケストレータLLMが
      分析結果から役割と focus を生成

## Phase 4+ — 拡張

- [ ] Optuna 統合(`tuning_trials` テーブルは既にあるが未使用)
- [ ] ビジュアライザ画像のマルチモーダル入力(弱点シードの絵をプロンプトへ)
- [ ] モデルポートフォリオ / バンディットによる LLM・役割の選択学習
- [ ] ダッシュボード(世代・系統樹・スコア推移の可視化)
- [ ] 問題セットアップの自動化(statement 取得 → config/tools 配置)
- [ ] ALE-Bench 的なメタ評価(過去AHC問題群でのシステム自体の性能測定)
- [ ] リモート評価(評価をローカルCPUから切り離す)
- [ ] コンテスト・コパイロットモード(rated 対応の human-in-the-loop UI)
