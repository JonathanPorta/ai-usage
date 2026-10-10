import AIUsageCore
import SwiftUI

public typealias T = DesignTokens

public extension Font {
    static let auCaption = Font.system(size: T.FontSize.caption)
    static let auChart = Font.system(size: T.FontSize.chart)
    static let auBody = Font.system(size: T.FontSize.body)
    static let auLead = Font.system(size: T.FontSize.lead, weight: .semibold)
    static let auTitle = Font.system(size: T.FontSize.title, weight: .semibold)
    static let auFigure = Font.system(size: T.FontSize.figure, weight: .medium).monospacedDigit()
}

/// Panel with a hairline border: the card container used across the popover and History.
public struct Panel<Content: View>: View {
    private let padding: CGFloat
    private let content: Content

    public init(padding: CGFloat = T.Space.s3, @ViewBuilder content: () -> Content) {
        self.padding = padding
        self.content = content()
    }

    public var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(T.Color.cardBackground.color, in: RoundedRectangle(cornerRadius: T.Radius.card))
            .overlay(RoundedRectangle(cornerRadius: T.Radius.card).strokeBorder(T.Color.cardBorder.color, lineWidth: 1))
    }
}

/// Upper-case section label ("QUOTA", "TODAY", "MODELS TODAY").
public struct SectionLabel: View {
    private let text: String
    public init(_ text: String) { self.text = text }
    public var body: some View {
        Text(text.uppercased())
            .font(.system(size: 11, weight: .semibold))
            .tracking(0.4)
            .foregroundStyle(T.Color.muted.color)
            .accessibilityAddTraits(.isHeader)
    }
}

/// Figure plus caption. "Not reported" renders a dash, never zero.
public struct StatTile: View {
    private let value: String
    private let caption: String
    public init(value: String, caption: String) {
        self.value = value
        self.caption = caption
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.system(size: 17, weight: .medium).monospacedDigit()).foregroundStyle(T.Color.text.color)
            Text(caption).font(.auCaption).foregroundStyle(T.Color.muted.color).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(T.Space.s3)
        .background(T.Color.cardBackground.color, in: RoundedRectangle(cornerRadius: T.Radius.medium))
        .overlay(RoundedRectangle(cornerRadius: T.Radius.medium).strokeBorder(T.Color.cardBorder.color, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

/// Horizontal meter (quota windows, model shares).
public struct Meter: View {
    private let fraction: Double
    private let tint: Color
    public init(fraction: Double, tint: Color = T.Color.meterFill.color) {
        self.fraction = fraction
        self.tint = tint
    }

    public var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(T.Color.meterTrack.color)
                Capsule().fill(tint).frame(width: max(0, min(1, fraction)) * proxy.size.width)
            }
        }
        .frame(height: 5)
        .accessibilityHidden(true)
    }
}

/// Small segmented control matching the prototype's `.seg` buttons.
public struct Segmented<Value: Hashable>: View {
    private let options: [(Value, String)]
    @Binding private var selection: Value
    private let label: String

    public init(_ label: String, selection: Binding<Value>, options: [(Value, String)]) {
        self.label = label
        self._selection = selection
        self.options = options
    }

    public var body: some View {
        Picker(label, selection: $selection) {
            ForEach(options, id: \.0) { option in Text(option.1).tag(option.0) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .controlSize(.small)
        .fixedSize()
    }
}

/// Status glyph + text in a tone. Color is never the only signal.
public struct ToneLabel: View {
    public enum Tone { case healthy, warning, danger, muted, accent }
    private let symbol: String
    private let text: String
    private let tone: Tone

    public init(_ text: String, symbol: String, tone: Tone) {
        self.text = text
        self.symbol = symbol
        self.tone = tone
    }

    public static func color(_ tone: Tone) -> Color {
        switch tone {
        case .healthy: return T.Color.success.color
        case .warning: return T.Color.warning.color
        case .danger: return T.Color.danger.color
        case .muted: return T.Color.muted.color
        case .accent: return T.Color.accent.color
        }
    }

    public var body: some View {
        Label {
            Text(text)
        } icon: {
            Image(systemName: symbol).foregroundStyle(Self.color(tone))
        }
        .font(.auCaption)
        .foregroundStyle(tone == .warning || tone == .danger ? Self.color(tone) : T.Color.muted.color)
    }
}
