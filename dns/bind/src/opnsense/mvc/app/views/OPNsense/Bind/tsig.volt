<span id="keygen_div" style="display:none" class="pull-right"><button id="keygen" type="button" class="btn btn-secondary" title="{{ lang._('Generate a random base64 key.') }}"><i class="fa fa-fw fa-gear"></i></button></span>
<div class="content-box"><div class="table-responsive"><table id="grid-tsig-keys" class="table table-condensed table-hover table-striped" data-editDialog="dialogEditBindTsig"><thead><tr><th data-column-id="enabled" data-formatter="rowtoggle">{{ lang._('Enabled') }}</th><th data-column-id="name">{{ lang._('Name') }}</th><th data-column-id="algorithm">{{ lang._('Algorithm') }}</th><th data-column-id="uuid" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th><th data-column-id="commands" data-formatter="commands">{{ lang._('Commands') }}</th></tr></thead><tbody></tbody><tfoot><tr><td colspan="5"><button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-plus"></span> {{ lang._('Add') }}</button></td></tr></tfoot></table></div></div>
{{ partial("layout_partials/base_dialog",['fields':formDialogEditBindTsig,'id':'dialogEditBindTsig','label':lang._('Edit TSIG Key')])}}
<script>
$(document).ready(function() {
    $('#grid-tsig-keys').UIBootgrid({search:'/api/bind/tsig/search_key',get:'/api/bind/tsig/get_key/',set:'/api/bind/tsig/set_key/',add:'/api/bind/tsig/add_key/',del:'/api/bind/tsig/del_key/',toggle:'/api/bind/tsig/toggle_key/'});
    $('#control_label_key\\.secret').append($('#keygen_div').detach().show());
    $('#keygen').click(function() { ajaxGet('/api/bind/tsig/generate/', {}, function(data) { if (data && data.secret) { $('#key\\.secret').val(data.secret).trigger('change'); }}); });
});
</script>
