import AIUsageCore
import SwiftUI

/// The pinned status row. Healthy is one quiet line; other states get their own tone.
public struct StatusRow: View {
    private let summary: StatusSummary
    private let reduceMotion: Bool

    public init(summary: StatusSummary, reduceMotion: Bool) {
        self.summary = summary
        self.reduceMotion = reduceMotion
    }

    public var body: some View {
        HStack(spacing: 6) {
            icon
            (Text(summary.title).fontWeight(.semibold) + Text(summary.detail.isEmpty ? "" : " · \(summary.detail)"))
                .font(.auBody)
                .foregroundStyle(T.Color.text.color)
                .lineLimit(2)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, T.Space.s3)
        .padding(.vertical, T.Space.s2)
        .background(background, in: RoundedRectangle(cornerRadius: T.Radius.medium))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Status: \(summary.title). \(summary.detail)")
    }

    @ViewBuilder
    private var icon: some View {
        switch summary.tone {
        case .healthy: Image(systemName: Symbols.healthy).foregroundStyle(T.Color.success.color)
        case .collecting:
            if reduceMotion {
                Image(systemName: Symbols.collect).foregroundStyle(T.Color.accent.color)
            } else {
                ProgressView().controlSize(.mini)
            }
        case .attention: Image(systemName: Symbols.failure).foregroundStyle(T.Color.danger.color)
        case .stopped: Image(systemName: Symbols.stopped).foregroundStyle(T.Color.danger.color)
        case .waiting: Image(systemName: Symbols.waiting).foregroundStyle(T.Color.muted.color)
        }
    }

    private var background: Color {
        switch summary.tone {
        case .attention, .stopped: return T.Color.dangerBackground.color
        default: return .clear
        }
    }
}

/// The single prominent notice, plus "+N more" when others are collapsed.
public struct NoticeBanner: View {
    private let notice: Presentation.Notice
    private let more: Int
    private let action: (() -> Void)?

    public init(notice: Presentation.Notice, more: Int, action: (() -> Void)?) {
        self.notice = notice
        self.more = more
        self.action = action
    }

    private var symbol: String {
        switch notice.tone {
        case .failure: return notice.title.contains("sign in") ? Symbols.signIn : Symbols.failure
        case .limit: return Symbols.limit
        case .stale: return Symbols.stale
        case .info: return Symbols.info
        }
    }

    private var foreground: Color {
        notice.tone == .failure ? T.Color.danger.color : notice.tone == .info ? T.Color.accent.color : T.Color.warning.color
    }

    private var background: Color {
        notice.tone == .failure ? T.Color.dangerBackground.color : notice.tone == .info ? T.Color.accentBackground.color : T.Color.warningBackground.color
    }

    public var body: some View {
        HStack(alignment: .top, spacing: T.Space.s2) {
            Image(systemName: symbol).foregroundStyle(foreground)
            VStack(alignment: .leading, spacing: 2) {
                Text(notice.title).font(.auBody.weight(.semibold)).foregroundStyle(T.Color.text.color)
                Text(notice.detail).font(.auCaption).foregroundStyle(T.Color.muted.color).lineLimit(3)
                if more > 0 {
                    Text("+\(more) more").font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
            Spacer(minLength: 0)
            if let action {
                Button("Details", action: action).buttonStyle(.link).font(.auCaption)
            }
        }
        .padding(T.Space.s3)
        .background(background, in: RoundedRectangle(cornerRadius: T.Radius.medium))
        .accessibilityElement(children: .contain)
    }
}
