package cmd

import (
	"context"
	"fmt"
	"github.com/gotd/td/telegram"
	"github.com/iyear/tdl/app/chat"
	"github.com/iyear/tdl/app/stl"
	"github.com/iyear/tdl/core/stltransport"
	"github.com/iyear/tdl/core/storage"
	"github.com/spf13/cobra"
	"os"
	"path/filepath"
)

func NewSTL() *cobra.Command {
	cmd := &cobra.Command{Use: "stl", Short: "Local STL integration", GroupID: groupTools.ID}
	executable, _ := os.Executable()
	var sourcePath string
	cmd.PersistentFlags().StringVar(&sourcePath, "source", filepath.Clean(filepath.Join(filepath.Dir(executable), "../../data/source.json")), "Private source configuration file")
	var output string
	servers := &cobra.Command{Use: "servers", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, args []string) error {
		if handled, err := proxySTL(cmd, sourcePath); handled {
			return err
		}
		return tRun(cmd.Context(), func(ctx context.Context, c *telegram.Client, kv storage.Storage) error {
			return stl.WriteJSON(output, map[string]any{"endpoints": stl.Servers(c)})
		})
	}}
	servers.Flags().StringVar(&output, "output", "", "Endpoint JSON output")
	servers.MarkFlagRequired("output")
	var filesOutput string
	var filesTopic int
	var filesAfter int
	files := &cobra.Command{Use: "files", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, args []string) error {
		if handled, err := proxySTL(cmd, sourcePath); handled {
			return err
		}
		if filesTopic <= 0 || filesAfter < 0 || filesAfter >= 2147483647 {
			return fmt.Errorf("an explicit approved-group topic is required")
		}
		source, err := stl.LoadSource(sourcePath)
		if err != nil {
			return err
		}
		return tRun(cmd.Context(), func(ctx context.Context, c *telegram.Client, kv storage.Storage) error {
			lastID := filesAfter
			err := chat.Export(ctx, c, kv, chat.ExportOptions{Type: chat.ExportTypeId, Chat: fmt.Sprint(source.ChatID), Thread: filesTopic, Input: []int{filesAfter + 1, 2147483646}, Output: filesOutput, Filter: "true", Raw: true, AfterID: filesAfter, LastID: &lastID})
			if err != nil {
				return err
			}
			if err := ctx.Err(); err != nil {
				return err
			}
			if err := stl.WriteJSON(filesOutput+".cursor", map[string]any{"after": filesAfter, "last_id": lastID, "topic": filesTopic}); err != nil {
				return err
			}
			return stl.WriteJSON(filesOutput+".complete", map[string]any{"complete": true, "topic": filesTopic})
		}, limiter)
	}}
	files.Flags().IntVar(&filesTopic, "topic", 0, "Approved group topic ID")
	files.Flags().IntVar(&filesAfter, "after-id", 0, "Read only messages newer than the last successful check")
	files.Flags().StringVar(&filesOutput, "output", "", "Exact metadata output")
	files.MarkFlagRequired("topic")
	files.MarkFlagRequired("output")
	var o stl.Options
	var events string
	download := &cobra.Command{Use: "download", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, args []string) error {
		if handled, err := proxySTL(cmd, sourcePath); handled {
			return err
		}
		source, err := stl.LoadSource(sourcePath)
		if err != nil {
			return err
		}
		o.ChatID = source.ChatID
		e, err := stl.OpenEvents(events)
		if err != nil {
			return err
		}
		defer e.Close()
		resolver := &stltransport.Resolver{}
		err = tRun(stltransport.With(cmd.Context(), resolver), func(ctx context.Context, c *telegram.Client, kv storage.Storage) error {
			return stl.Download(ctx, c, kv, resolver, o, e)
		})
		if err != nil {
			_ = e.Emit("error", map[string]any{"message": err.Error()})
		}
		return err
	}}
	f := download.Flags()
	f.IntVar(&o.Topic, "topic", 0, "Approved topic ID")
	f.IntVar(&o.Message, "message", 0, "Message ID")
	f.IntVar(&o.DC, "dc", 0, "Expected file data center")
	f.Int64Var(&o.DocumentID, "document-id", 0, "Expected document ID")
	f.Int64Var(&o.Size, "size", 0, "Expected exact byte count")
	f.StringVar(&o.Filename, "filename", "", "Expected filename")
	f.StringVar(&o.Output, "output", "", "Exclusive local output file")
	f.StringVar(&events, "events", "", "Exclusive JSON event log")
	f.StringVar(&o.Server, "server", "auto", "Auto or advertised endpoint key")
	f.StringVar(&o.Cache, "cache", "", "Server speed cache")
	f.StringVar(&o.Network, "network", "default", "Network cache identity")
	f.BoolVar(&o.IPv6, "ipv6", false, "IPv6 route available")
	f.BoolVar(&o.Retest, "retest", false, "Ignore cached server comparison")
	f.BoolVar(&o.QuickTest, "quick-test", false, "Use two-second samples per endpoint")
	f.StringVar(&o.ReuseServer, "reuse-server", "", "Reuse this release's automatic selection; retest on failure")
	f.BoolVar(&o.ProbeOnly, "probe-only", false, "Only compare servers; retain no payload")
	for _, name := range []string{"topic", "message", "dc", "document-id", "size", "filename", "output", "events", "cache"} {
		download.MarkFlagRequired(name)
	}
	cmd.AddCommand(servers, files, download, newSTLService(&sourcePath))
	return cmd
}
