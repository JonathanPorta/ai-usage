import AIUsageCore
import AIUsageDesign
import AppKit
import SwiftUI

enum PopoverScreen: Equatable {
    case detail(String)
    case monitoring
    case settings
}

/// Popover: 420 pt wide, height ≤ min(780, screen − 96). Header, status row,
/// notice and footer stay pinned; only the card list (or screen body) scrolls.
/// Detail, Monitoring and Settings replace the overview; the overview stays
/// mounted underneath so Back restores its scroll position and focus.
struct PopoverRoot: View {
    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var screen: PopoverScreen?
    @State private var returnFocus: String?
    private let detailMetric: ProviderDetailView.Metric
    private let screenHeight: CGFloat?

    init(initialScreen: PopoverScreen? = nil, detailMetric: ProviderDetailView.Metric = .tokens, screenHeight: CGFloat? = nil) {
        _screen = State(initialValue: initialScreen)
        self.detailMetric = detailMetric
        self.screenHeight = screenHeight
    }

    private var maxHeight: CGFloat {
        let screen = screenHeight ?? NSScreen.main?.visibleFrame.height ?? 900
        return min(T.Layout.popoverMaxHeight, screen - T.Layout.popoverScreenMargin)
    }

    var body: some View {
        // Ticks once a minute so relative times ("Checked 2 min ago") stay live between report refreshes.
        TimelineView(.everyMinute) { _ in content }
    }

    private var content: some View {
        ZStack {
            OverviewView(openDetail: { screen = .detail($0) }, openHistory: showHistory,
                         openMonitoring: { screen = .monitoring }, openSettings: { screen = .settings },
                         returnFocus: $returnFocus, maxHeight: maxHeight)
                .opacity(screen == nil ? 1 : 0)
                .allowsHitTesting(screen == nil)
                .accessibilityHidden(screen != nil)
            switch screen {
            case let .detail(id)?:
                if let provider = store.report?.provider(id) {
                    ProviderDetailView(provider: provider, back: back, openHistory: { showHistory(id) },
                                       maxHeight: maxHeight, initialMetric: detailMetric)
                        .transition(transition)
                }
            case .monitoring?:
                MonitoringView(back: back, openDetail: { screen = .detail($0) }, maxHeight: maxHeight)
                    .transition(transition)
            case .settings?:
                SettingsView(back: back, maxHeight: maxHeight)
                    .transition(transition)
            case nil:
                EmptyView()
            }
        }
        .frame(width: T.Layout.popoverWidth)
        .background(T.Color.background.color)
        .animation(reduceMotion ? nil : .easeOut(duration: T.Duration.medium), value: screen)
        .onAppear { store.popoverOpened() }
        .background {
            // ⌘, opens Settings from anywhere in the popover.
            Button("") { screen = .settings }.keyboardShortcut(",", modifiers: .command).hidden()
        }
    }

    private var transition: AnyTransition {
        reduceMotion ? .identity : .move(edge: .trailing).combined(with: .opacity)
    }

    private func back() {
        if case let .detail(id)? = screen { returnFocus = id }
        screen = nil
    }

    private func showHistory(_ providerId: String?) {
        navigation.historyRequest = HistoryRequest(providerId: providerId)
        openWindow(id: HistoryView.windowId)
        NSApp.activate(ignoringOtherApps: true)
    }
}

/// Measures a view's height into a binding (used to size the scroll region to content).
struct HeightReader: ViewModifier {
    @Binding var height: CGFloat
    func body(content: Content) -> some View {
        content.background(GeometryReader { proxy in
            Color.clear
                .onAppear { height = proxy.size.height }
                .onChange(of: proxy.size.height) { _, value in height = value }
        })
    }
}

extension View {
    func readHeight(_ height: Binding<CGFloat>) -> some View { modifier(HeightReader(height: height)) }
}
