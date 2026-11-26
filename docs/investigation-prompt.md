OmniWM v0.8.0 の Intel Mac 互換版を開発するため、まずソースコードの監査と実装計画の策定を行ってください。

## Context

- `docs/intel-compatibility.md` を最初に読み、要求仕様の Source of Truth としてください。
- 現在のリポジトリは fork 済み、jj colocated 構成、v0.8.0 (`bf118cca`) を親とする空の作業コピーから開始しています（仕様書を追加した場合はその差分のみ存在します）。
- 既存の agent instructions、CONTRIBUTING、README、ビルド・CI関連文書を確認し、既存の規約を尊重してください。

## Investigation

1. **Baseline & history**: `jj` で現在の状態・remote・タグを確認し、v0.5.2.1 → v0.5.3 の Intel 対応廃止差分と、それ以降の関連変更を調査する。
2. **Architecture & toolchain**: arm64 固定、アーキテクチャ依存コード、SwiftPM、Swift/C/C++/Zig、Xcode/SDK、deployment target、ネイティブ依存関係を監査する。ビルドホストとターゲットを区別し、x86_64 生成・リンクが可能か確認する。
3. **GhosttyKit & dependencies**: 上流の固定リビジョン、Zig バージョン、パッチ、Universal/x86_64 ビルド経路、XCFramework のスライス、ABI・ヘッダー・静的リンク・チェックサム、Luau その他の依存関係を確認する。
4. **Runtime & features**: Window Management、Quake Terminal、OmniShell、Metal Effects、FoundationModels、Hidden Bar、その他の OS/CPU/GPU/Private API 依存箇所を監査する。コンパイル時と実行時の制約、既存のフォールバック、UI・設定・CLI・IPC からの呼び出しを確認し、仕様書の全機能の維持可否を整理する。
5. **CI & packaging**: 公式ワークフローを確認し、fork から利用できる runner・ツールチェーン・権限・費用・依存取得・キャッシュ・アーティファクト生成の条件を検証する。クロスコンパイル、署名、Bundle ID、権限、最低OS、Mach-O の検査、設定引き継ぎ・ロールバックを検討する。arm64 ビルドへの影響も評価する。
6. **Updater**: 実装を追跡し、更新検知・通知と公式バイナリの適用を分離できるか調査する。自動・手動トリガーを含め、公式 arm64 バイナリによる置き換えが起きないようにする最小変更を提案する。
7. **Alternatives & risks**: 上流と同じツールチェーンでの CI クロスコンパイルを優先し、成立しない場合の代替案、阻害要因、コスト、修正範囲を比較する。先行調査の前提を鵜呑みにせず、ローカルの該当タグと一次資料で再確認する。

## Deliverables

日本語で次の内容を報告してください。

- 現在のリポジトリの状態と調査の結論
- ビルド阻害要因（コンパイル、リンク、パッケージング、実行時を区別）と根拠
- 機能別の互換性マトリクス（維持可能 / 条件付き / 対応困難 / 未確認）
- 修正対象ファイル、修正理由、最小変更案、上流への影響
- runner、toolchain、依存バージョンと再現可能な CI の設計案
- 段階的な実装計画（依存ライブラリのビルド → 本体 → パッケージ → 実機検証を基本とし、検証ゲートを設定）
- 仕様書の Acceptance Criteria に対応するテスト計画と残存リスク
- 私の判断が必要な未確定事項・仕様変更案

ソースのファイルパス・行番号・コミット、外部資料のURLを根拠として示してください。確認済みの事実、静的調査からの推定、実験でしか確認できない事項を区別してください。必要に応じて公式ドキュメントや関連リポジトリ、Issuesを調査してください。

## Scope of This Session

- Plan Mode で読み取り専用の調査を行う。リポジトリ内のファイル編集、依存関係のインストール、ビルド、VCS の書き込み操作、push、GitHub Actions の実行は行わない。
- VCS 操作には jj、GitHub 操作には gh、リポジトリの取得・管理には ghq を使用する。git コマンドを直接使用しない。
- 要求仕様を独断で変更したり、維持が困難と判断した機能を削除したりしない。
- 調査結果と計画案を提示した時点で停止し、レビューを待つ。承認後に `docs/intel-build-plan.md` として保存し、実装へ進む。既存の agent instructions の追記が必要ならその内容も提案する。
