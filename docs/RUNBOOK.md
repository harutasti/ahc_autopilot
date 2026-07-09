# Autopilot 運用 Runbook

実戦セッションの回し方・監視・トラブル対応の手順書。現在地と優先順位は
[Tracking issue #13](https://github.com/harutasti/ahc_autopilot/issues/13) を参照。

## 実戦セッションの起動(claude CLI アダプタ)

```bash
IS_SANDBOX=1 AHC_ADAPTER_TIMEOUT_SEC=1800 python3 tools/ahc.py autopilot \
  --problem ahc067 \
  --seeds 0-29 --validation-seeds 30-39 \
  --generations 2 --max-trials 2 --agents 2 \
  --dynamic-roles --archive --repair-attempts 1 --budget 45m \
  --adapter 'claude --model sonnet --dangerously-skip-permissions -p "$(cat {prompt})"'
```

要点:

- **`IS_SANDBOX=1` は root 環境で必須**。ないと claude CLI が
  `--dangerously-skip-permissions cannot be used with root/sudo privileges`
  で全トライアル失敗する(fatal 分類で即中断されるようになったが、起動前に付けるのが正)
- **`AHC_ADAPTER_TIMEOUT_SEC`**: エージェント1呼び出しの上限秒。実装系タスクは
  1800 推奨(900 だと大きめの実装が時間切れ)。タイムアウトしてもワークスペースに
  変更があれば自動で評価に回収される
- **usage limit**: claude CLI のセッション上限に当たると fatal 分類でセッションが
  `adapter_fatal` 終了する。リセット時刻(例: 3am UTC)後に再実行
- パラメータ狙い撃ちの例(役割固定・修正リトライ増):
  `--roles ParameterTuner --max-trials 1 --repair-attempts 2 --seeds 0-59 --validation-seeds 60-79 --focus "..."`

## 進行の監視

セッションはバックグラウンドで回し、DB を読む:

```bash
# セッション状態とトライアル判定
python3 - <<'EOF'
import json, sqlite3
conn = sqlite3.connect("experiments/ahc.sqlite3")
conn.row_factory = sqlite3.Row
sid = conn.execute("select max(id) as m from autopilot_sessions").fetchone()["m"]
print(conn.execute("select id, status from autopilot_sessions where id=?", (sid,)).fetchone()[1])
for t in conn.execute("select id, role, status, decision from autopilot_trials where session_id=?", (sid,)):
    print(dict(t))
EOF

# エージェントの生ログ
ls experiments/adapter_logs/            # trial_N(.repair系)/orchestrator_* の stdout/stderr
# オーケストレータが生成した役割
# agent_messages テーブル (agent_name='Orchestrator', role='roles')
```

## セッション後にやること

1. マージがあれば `solver/main.cpp` と `knowledge/<problem>_autopilot.md` の差分をコミット
2. マージゼロでも insights(knowledge 追記)はコミットして次セッションに還流させる
3. 大きな学び(方向性の裁定など)は `knowledge/<problem>.md` に手動で追記する
   — オーケストレータは knowledge 検索経由でこれを読む

## 判定の読み方

- `accepted` → 検証シードも通ればマージ。`summary_json` の `mean_effective_delta`,
  `improve_confidence`, `validation` を確認
- `rejected` → `acceptance.reasons` に理由。修正ループ有効時は `repair.attempts` に経過
- `duplicate` → novelty フィルタが評価前にスキップ(`duplicate.run_id` が重複先)
- `error` + `summary.fatal` → アダプタが回復不能(レート制限・認証・設定)。
  セッションは `adapter_fatal` で終了している
- `[timing_remeasure]` が summary にあれば経過時間の再計測バルブが発動している

## ゲート調整(問題ごと)

`problems/<problem>/config.yaml` の `acceptance:` ブロック。ahc067 は現在
「悪化1シードまで(悪化幅150k以内)+ improve_confidence ≥ 0.9」に緩和済み。
