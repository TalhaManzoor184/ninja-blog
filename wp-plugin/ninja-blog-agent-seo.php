<?php
/**
 * Plugin Name: Ninja Blog Agent – SEO fields
 * Description: Lets the PowerApps Ninja blog agent set the Yoast SEO / Rank Math title, meta description and focus keyword through the REST API. Only users who can edit the post can write these fields.
 * Version: 1.0.0
 * Author: PowerApps Ninja
 */

if (!defined('ABSPATH')) {
    exit;
}

add_action('init', function () {
    $keys = array(
        '_yoast_wpseo_title',
        '_yoast_wpseo_metadesc',
        '_yoast_wpseo_focuskw',
        'rank_math_title',
        'rank_math_description',
        'rank_math_focus_keyword',
    );
    foreach ($keys as $key) {
        register_post_meta('post', $key, array(
            'type'              => 'string',
            'single'            => true,
            'show_in_rest'      => true,
            'sanitize_callback' => 'sanitize_text_field',
            'auth_callback'     => function ($allowed, $meta_key, $post_id) {
                return current_user_can('edit_post', $post_id);
            },
        ));
    }
});
